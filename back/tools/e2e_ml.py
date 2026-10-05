"""Verificación e2e del motor de detección contra el stack real, con lotes reales.

Recorre el camino de producción completo, sin mocks:

    ECG sintético con escenario (foco ectópico, taquicardia, pausa, electrodo suelto)
      → codificado a tramas Rice de 256 B (el MISMO codec del firmware)
      → POSTs seguidos de hasta 48 tramas a /ingest/ecg-frames, con las cabeceras
        que manda el puente ESP32-C3 (hora de pared, uptime, bootId, STATUS)
      → go-back-N: cada POST arranca en `lastAcceptedSeq + 1` (recortado al final
        del lote, y sin confirmar nada si se adelanta más de 512 tramas, como el
        puente); un 503 espera su `Retry-After`; un 5xx o una respuesta que tarda
        más de 15 s se reintenta mientras la racha dure menos de 60 s
      → POST /studies/{id}/complete con la sesión del médico
      → espera a que `ml_analyzed_samples` y `beats_analyzed_samples` alcancen
        `samples_count`
      → GET /studies/{id}/findings y GET /studies/{id}/holter-metrics

Es la contraparte scripteable del simulador de chaleco del portal: genera el
mismo tipo de señal y usa el mismo codec, pero se puede correr desde la terminal
y contrastar los números contra la verdad del escenario.

Uso (desde `back/`):

    uv run python -m tools.e2e_ml                        # 20 min, ectópico cada 12, suelto en 300 s
    uv run python -m tools.e2e_ml --dry-run --minutes 2  # arma los lotes, sin red ni base
    uv run python -m tools.e2e_ml --minutes 30 --tachy-at 600:120:150 --pause-at 900:3.2
    uv run python -m tools.e2e_ml --cadence 15           # un POST cada 15 s, como el puente en vivo
    uv run python -m tools.e2e_ml --drop-frame-every 5   # fuerza retransmisiones go-back-N

El comportamiento anterior (un solo POST, estudio creado a mano en la base, sin
cabeceras de hora y sin cierre) sigue disponible:

    uv run python -m tools.e2e_ml --single-post --seed-study --no-time-sync --no-close

Código de salida: 0 si todo se ingirió y se analizó; 1 si la ingesta se cortó,
algún lote quedó FAILED, el cierre falló, faltan muestras, el cursor del análisis
se trabó sin lote FAILED (`--stall-timeout`: una pasada falló dentro de su
SAVEPOINT, ver los logs) o el análisis no terminó dentro de `--timeout`.

Las tramas salen con el bit de dato simulado (`hdrFlags` bit 3) a propósito: el
estudio queda `is_simulated` y el informe clínico avisa `SIMULATED_STUDY`, así una
corrida contra una base compartida no pasa por un estudio clínico. El puente real
manda tramas sin ese bit; `--no-simulated` las manda así. El análisis no cambia:
el bit solo marca el estudio.

No es un test de CI: necesita la base, MinIO y la API levantados (salvo con
`--dry-run`). Vive en `tools/` y no en `app/` justamente por eso. Deja en la base
el médico, el paciente, el equipo y el estudio que crea (`e2e-…@holter.test`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.models.device import Device, DeviceStatus  # noqa: E402
from app.db.models.doctor import Doctor  # noqa: E402
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus  # noqa: E402
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus  # noqa: E402
from app.db.models.study import Study, StudyStatus  # noqa: E402
from app.db.models.user import IdentityStatus, User, UserRole  # noqa: E402
from app.ml.decompression import FLAG_LEAD_OFF, FLAG_R_PEAK, STEP_MS  # noqa: E402
from app.ml.frame_header import read_header  # noqa: E402
from tests.ecg_synth import (  # noqa: E402
    FIRMWARE_R_PEAK_LAG_MS,
    SAMPLE_RATE,
    _beat,
    to_microvolts,
)
from tests.frame_builder import Sample, encode_samples  # noqa: E402

#: Tope de tramas por POST del puente ESP32-C3: "hasta 48 tramas (12 kB)", las
#: que el equipo tiene en vuelo (`INTEGRACION.md` §11.3). ~15 s de señal real.
BRIDGE_MAX_FRAMES = 48
FRAME_BYTES = 256
FIRMWARE_VERSION = "1.4.2"
#: Lo que declara `BridgeTimeSync.h` con una sincronización SNTP fresca.
TIME_SYNC_UNCERTAINTY_MS = 200
#: Bit 5 de `leadOffFlags` (§3.1): electrodo suelto detectado por la señal.
LEAD_FLAGS_SIGNAL_LEAD_OFF = 0x20
#: Bit 0 de `batteryFlags`: el equipo informa la batería. Sin él, el puente
#: omite `X-Battery-Pct` y los demás bits no significan nada.
BATTERY_FLAGS_REPORTED = 0x01
BATTERY_PCT = 87
RSSI_DBM = -58
#: Cuánto antes del primer POST terminó de grabarse la última muestra.
ANCHOR_MARGIN_MS = 2_000
#: ACKs seguidos sin avanzar el cursor antes de dar la ingesta por trabada.
MAX_STALLED_ACKS = 5
#: Cuánto puede adelantarse `lastAcceptedSeq` al lote mandado sin que el puente lo
#: dé por inexplicable (`HOLTER_LINK_MAX_ACK_LOOKAHEAD_FRAMES`, `INTEGRACION.md`
#: §11.3 y §11.6). Más que eso no lo explica ninguna retransmisión: no se confirma
#: nada.
MAX_ACK_LOOKAHEAD_FRAMES = 512
#: Intentos con un ACK inexplicable antes de que el puente cierre la ventana.
INEXPLICABLE_ACK_ATTEMPTS = 3
#: Lo que el puente espera la respuesta de cada POST (§11.3).
BRIDGE_POST_TIMEOUT_S = 15.0
#: Timeout de los pedidos del médico (cierre, hallazgos, métricas): no son del puente.
API_TIMEOUT_S = 60.0
#: Encuestas seguidas sin cambios para dar por asentado un estudio abierto.
SETTLE_POLLS = 3
SESSION_COOKIE = "holter_session_v2"


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# Escenario y señal
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Scenario:
    minutes: float
    bpm: float
    ectopic_every: int
    #: `(inicio_s, duración_s, lpm)`: el R-R base pasa a `60 / lpm` en el tramo.
    tachy: tuple[tuple[float, float, float], ...]
    #: `(segundo, rr_s)`: el R-R que arranca en el primer latido desde ese
    #: segundo dura `rr_s`.
    pauses: tuple[tuple[float, float], ...]
    #: `(inicio_s, duración_s)` con `FLAG_LEAD_OFF` sobre las muestras.
    lead_off: tuple[tuple[float, float], ...]
    #: Además de la marca, la señal del tramo queda sin ECG (solo ruido).
    lead_off_flat: bool
    noise_uv: float
    seed: int

    @property
    def duration_s(self) -> float:
        return self.minutes * 60.0

    def bpm_at(self, time_s: float) -> float:
        for start, duration, bpm in self.tachy:
            if start <= time_s < start + duration:
                return bpm
        return self.bpm


@dataclass(frozen=True)
class Truth:
    """La verdad de referencia del escenario, para contrastar con lo que mide la API."""

    #: Picos R que un detector puede ver (sin los de un tramo suelto y plano).
    rpeaks: np.ndarray
    ectopic: np.ndarray
    #: R-R entre latidos visibles consecutivos. El que cruza un tramo plano no
    #: es una pausa del paciente: es señal que no existe, y se deja afuera.
    rr_ms: np.ndarray
    lead_off_s: float
    duration_s: float


def synth_scenario(scenario: Scenario) -> tuple[np.ndarray, np.ndarray, Truth]:
    """El generador de `tests.ecg_synth.synth_ecg`, con R-R variable.

    Sin taquicardia ni pausas da la MISMA señal, muestra a muestra, que
    `synth_ecg(duration_s, ectopic_every=…)`: mismo latido, misma secuencia de
    R-R, mismo ruido con la misma semilla. Lo que agrega es que el período sale
    de `bpm_at` (los tramos de taquicardia) y que una pausa reemplaza el R-R que
    arranca en el primer latido posterior a su segundo, incluida la pausa
    compensatoria de un ectópico si coincide.
    """
    rate = SAMPLE_RATE
    rng = np.random.default_rng(scenario.seed)
    n = int(scenario.duration_s * rate)
    signal = np.zeros(n, dtype=np.float64)
    flags = np.zeros(n, dtype=np.uint8)
    half = rate // 2
    lag = int(FIRMWARE_R_PEAK_LAG_MS * rate / 1000.0)
    every = scenario.ectopic_every
    pending_pauses = sorted(scenario.pauses)

    rpeaks: list[int] = []
    ectopics: list[int] = []
    time_s, index = 0.5, 0
    while time_s < scenario.duration_s - 1.0:
        is_ectopic = every > 0 and index % every == every - 1
        center = int(time_s * rate)
        low, high = max(center - half, 0), min(center + half, n)
        offsets = (np.arange(low, high) - center) / rate
        signal[low:high] += _beat(
            offsets,
            width=3.0 if is_ectopic else 1.0,
            invert_t=is_ectopic,
            amp=1.3 if is_ectopic else 1.0,
        )
        # Igual que `synth_ecg`: el flag va donde el MCU confirma el latido.
        if center + lag < n:
            flags[center + lag] |= FLAG_R_PEAK
        rpeaks.append(center)
        if is_ectopic:
            ectopics.append(center)

        period = 60.0 / scenario.bpm_at(time_s)
        following_is_ectopic = every > 0 and (index + 1) % every == every - 1
        if pending_pauses and time_s >= pending_pauses[0][0]:
            time_s += pending_pauses.pop(0)[1]
        elif following_is_ectopic:
            time_s += period * 0.6  # el ectópico llega antes de tiempo
        elif is_ectopic:
            time_s += period * 1.4  # pausa compensatoria
        else:
            time_s += period
        index += 1

    peaks = np.array(rpeaks, dtype=np.int64)
    visible = np.ones(peaks.size, dtype=bool)
    lead_off_samples = 0
    for start_s, duration_s in scenario.lead_off:
        low, high = int(start_s * rate), min(int((start_s + duration_s) * rate), n)
        if low >= high:
            continue
        lead_off_samples += high - low
        if scenario.lead_off_flat:
            # Electrodo despegado de verdad: no hay ECG, queda el ruido del
            # front-end, y el detector del MCU no confirma ningún latido.
            signal[low:high] = 0.0
            flags[low:high] &= np.uint8(~FLAG_R_PEAK & 0xFF)
            visible &= ~((peaks >= low) & (peaks < high))

    signal += rng.normal(0.0, scenario.noise_uv / 1000.0, n)
    # La marca va después del ruido, como en la versión anterior del script:
    # no toca la señal, solo dice dónde estuvo suelto.
    for start_s, duration_s in scenario.lead_off:
        low, high = int(start_s * rate), min(int((start_s + duration_s) * rate), n)
        if low < high:
            flags[low:high] |= FLAG_LEAD_OFF

    ectopic = np.array(ectopics, dtype=np.int64)
    truth = Truth(
        rpeaks=peaks[visible],
        ectopic=ectopic[np.isin(ectopic, peaks[visible])],
        rr_ms=(np.diff(peaks) * 1000.0 / rate)[visible[:-1] & visible[1:]],
        lead_off_s=lead_off_samples / rate,
        duration_s=n / rate,
    )
    return signal.astype(np.float32), flags, truth


# --------------------------------------------------------------------------- #
# Tramas, reloj del equipo y cabeceras del puente
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Frame:
    seq: int
    t0_ms: int
    end_ms: int
    n_samples: int
    lead_off: bool
    payload: bytes


def build_frames(
    signal_mv: np.ndarray, flags: np.ndarray, boot_id: int, *, simulated: bool = True
) -> list[Frame]:
    """Codifica con el codec del firmware y lee de vuelta la cabecera de cada trama.

    `simulated` prende el bit de dato simulado de `hdrFlags` (ver `--no-simulated`).
    """
    samples = [
        Sample(timestamp_ms=index * STEP_MS, raw_uV=[value], flags=int(flag))
        for index, (value, flag) in enumerate(zip(to_microvolts(signal_mv), flags, strict=True))
    ]
    frames: list[Frame] = []
    offset = 0
    for raw in encode_samples(samples, first_seq=0, boot_id=boot_id, simulated=simulated):
        info = read_header(raw)
        window = flags[offset : offset + info.n_samples]
        frames.append(
            Frame(
                seq=info.seq,
                t0_ms=info.t0_ms,
                end_ms=info.t0_ms + info.duration_ms,
                n_samples=info.n_samples,
                lead_off=bool(np.any(window & FLAG_LEAD_OFF)),
                payload=raw,
            )
        )
        offset += info.n_samples
    if offset != len(signal_mv):
        print(f"  ⚠ las tramas cubren {offset} muestras de {len(signal_mv)}")
    return frames


@dataclass(frozen=True)
class DeviceClock:
    """El reloj del equipo visto por el puente: `millis()` y el epoch SNTP del mismo instante.

    El arranque se ubica de modo que la última muestra se haya grabado
    `ANCHOR_MARGIN_MS` antes del primer POST: el equipo grabó todo y el puente
    drena el backlog, que es el caso normal del ciclo de 10 min. Así cada POST
    cumple `epoch − uptime + t0Ms ≤ recepción`, que es lo que la ingesta exige
    para usar el ancla del puente; si no, cae a `server_receive` y el estudio
    queda con la hora sin verificar. El ancla (`epoch − uptime`) es la misma en
    todos los POST, como en un equipo que no se reinició.
    """

    boot_epoch_ms: int

    def read(self, at_ms: int) -> tuple[int, int]:
        """`(epoch_ms, uptime_ms)` leídos juntos en `at_ms`."""
        return at_ms, at_ms - self.boot_epoch_ms


def bridge_headers(
    *,
    serial: str,
    api_key: str,
    clock: DeviceClock,
    at_ms: int,
    boot_id: int,
    batch: list[Frame],
    time_sync: bool,
) -> dict[str, str]:
    """Las cabeceras que manda el puente en cada POST (`INTEGRACION.md` §11.1).

    `X-Device-Sqi` se omite a propósito: el equipo la omite cuando no estimó
    ninguna, y este script no la estima.
    """
    epoch_ms, uptime_ms = clock.read(at_ms)
    backlog_s = min(max((uptime_ms - batch[0].t0_ms) // 1000, 0), 65_535)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Device-Serial": serial,
        "X-Device-Uptime-Ms": str(uptime_ms),
        "X-Device-Boot-Id": str(boot_id),
        "X-Firmware-Version": FIRMWARE_VERSION,
        "X-Battery-Pct": str(BATTERY_PCT),
        "X-Device-Battery-Flags": str(BATTERY_FLAGS_REPORTED),
        "X-Device-Lead-Flags": str(
            LEAD_FLAGS_SIGNAL_LEAD_OFF if any(frame.lead_off for frame in batch) else 0
        ),
        "X-Device-Loss-Flags": "0",
        "X-Device-Status-Flags": "0",
        "X-Device-Backlog-Seconds": str(backlog_s),
        "X-Device-Rssi": str(RSSI_DBM),
        "Content-Type": "application/octet-stream",
    }
    if time_sync:
        headers["X-Bridge-Epoch-Ms"] = str(epoch_ms)
        headers["X-Time-Sync-Source"] = "ntp"
        headers["X-Time-Sync-Uncertainty-Ms"] = str(TIME_SYNC_UNCERTAINTY_MS)
    return headers


def with_hole(batch: list[Frame]) -> list[Frame]:
    """El lote sin la trama del medio: el servidor confirma hasta el hueco."""
    middle = len(batch) // 2
    return batch[:middle] + batch[middle + 1 :]


# --------------------------------------------------------------------------- #
# Base: alta de médico, paciente, equipo (y estudio) y lectura de cursores
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Seeded:
    study_id: uuid.UUID | None
    serial: str
    api_key: str
    user: User


async def seed(
    factory: async_sessionmaker[AsyncSession], *, create_study: bool, started_at: datetime
) -> Seeded:
    """Crea médico, paciente y equipo asignado; el estudio, solo con `--seed-study`.

    Sin estudio es el flujo real: el chaleco empieza a mandar y la ingesta abre
    el estudio sola (`ingest_service._resolve_study`).
    """
    api_key = secrets.token_urlsafe(32)
    suffix = uuid.uuid4().hex[:8]

    async with factory() as db:
        user = User(
            email=f"e2e-{suffix}@holter.test",
            full_name="E2E Motor ML",
            role=UserRole.MEDICO,
            identity_status=IdentityStatus.ACTIVE,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        doctor = Doctor(user_id=user.id, license_number=f"MN-{suffix}")
        db.add(doctor)
        await db.flush()
        patient = Patient(
            doctor_id=doctor.id,
            medical_record_num=f"HC-{suffix}",
            first_name="Paciente",
            last_name=f"E2E {suffix}",
            date_of_birth=datetime(1970, 1, 1).date(),
            dni=str(30_000_000 + int(suffix[:6], 16) % 9_000_000),
            sex=PatientSex.M,
            study_status=PatientStudyStatus.ACTIVE,
        )
        db.add(patient)
        await db.flush()
        device = Device(
            serial_number=f"E2E-{suffix.upper()}",
            model="Holter ECG",
            api_key_hash=sha256(api_key.encode()).hexdigest(),
            patient_id=patient.id,
            doctor_id=doctor.id,
            status=DeviceStatus.ASSIGNED,
            firmware_version=FIRMWARE_VERSION,
        )
        db.add(device)
        await db.flush()
        study_id: uuid.UUID | None = None
        if create_study:
            study = Study(
                patient_id=patient.id,
                device_id=device.id,
                started_at=started_at,
                status=StudyStatus.IN_PROGRESS,
                sample_rate=SAMPLE_RATE,
            )
            db.add(study)
            await db.flush()
            study_id = study.id
        await db.commit()
        return Seeded(study_id, device.serial_number, api_key, user)


@dataclass(frozen=True)
class Progress:
    status: str
    samples: int
    ml: int
    beats: int
    batches: tuple[tuple[str, int], ...]

    def count(self, status: ProcessingStatus) -> int:
        return dict(self.batches).get(status.value, 0)

    @property
    def pending(self) -> int:
        return self.count(ProcessingStatus.PENDING) + self.count(ProcessingStatus.PROCESSING)

    @property
    def analyzed(self) -> bool:
        return self.samples > 0 and self.ml >= self.samples and self.beats >= self.samples

    def line(self) -> str:
        def pct(value: int) -> str:
            return f"{value / self.samples:6.1%}" if self.samples else "   -  "

        batches = " ".join(f"{name}={count}" for name, count in self.batches) or "sin lotes"
        return (
            f"{self.status:11s} muestras={self.samples:>9,d} · motor {pct(self.ml)} · "
            f"latidos {pct(self.beats)} · {batches}"
        )


async def read_progress(factory: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> Progress:
    async with factory() as db:
        row = (
            await db.execute(
                select(
                    Study.status,
                    Study.samples_count,
                    Study.ml_analyzed_samples,
                    Study.beats_analyzed_samples,
                ).where(Study.id == study_id)
            )
        ).one()
        counts = (
            await db.execute(
                select(ECGBatch.processing_status, func.count())
                .where(ECGBatch.study_id == study_id)
                .group_by(ECGBatch.processing_status)
            )
        ).all()
    batches = tuple(sorted((str(getattr(s, "value", s)), int(c)) for s, c in counts))
    status = str(getattr(row[0], "value", row[0]))
    return Progress(status, int(row[1]), int(row[2]), int(row[3]), batches)


def session_cookie(user: User) -> dict[str, str]:
    """Cookie de sesión firmada con el mismo secreto que usa la API.

    Se emite acá y no se pide por `/auth/login` porque ese camino pasa por
    Auth0, y el punto de este script es no depender de nada externo.
    """
    from app.core.security import create_access_token

    token, _ = create_access_token(user)
    return {SESSION_COOKIE: token}


# --------------------------------------------------------------------------- #
# Ingesta: el protocolo del puente
# --------------------------------------------------------------------------- #


@dataclass
class IngestStats:
    posts: int = 0
    acked: int = 0
    retries: int = 0
    busy: int = 0
    server_errors: int = 0
    resyncs: int = 0
    holes: int = 0
    clamped: int = 0
    inexplicable: int = 0
    duplicates: int = 0
    bytes_sent: int = 0
    latencies: list[float] = field(default_factory=list)
    study_ids: list[str] = field(default_factory=list)
    started: float = 0.0
    finished: float = 0.0
    error: str | None = None

    @property
    def elapsed(self) -> float:
        return self.finished - self.started


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(float(response.headers.get("Retry-After", "5")), 0.0)
    except ValueError:
        return 5.0


async def send_frames(
    client: httpx.AsyncClient,
    frames: list[Frame],
    *,
    serial: str,
    api_key: str,
    clock: DeviceClock,
    boot_id: int,
    max_frames: int,
    args: argparse.Namespace,
) -> IngestStats:
    """Manda la señal como el puente: lotes contiguos desde el cursor del servidor.

    El cursor es del servidor, no nuestro: cada POST arranca en
    `lastAcceptedSeq + 1` de la respuesta anterior (`INTEGRACION.md` §11.1, "el
    puente usa `lastAcceptedSeq`, no `framesAccepted`"). Un `503` es contención
    de lock (`main.database_contention_handler`): se espera el `Retry-After` y se
    reintenta el mismo lote. Un `5xx` o un POST sin respuesta —el puente espera
    cada respuesta `--post-timeout`, 15 s— se reintenta mientras la racha dure
    menos de `--retry-window`; pasado eso el equipo real cierra la ventana como
    fallida, y acá se corta. Un `4xx` la cierra en el acto.

    El ACK se lee como `HolterBridgeAck::aplicarRespuesta` del puente: un cursor
    por delante del lote se recorta al final del lote (lo que sigue se vuelve a
    mandar y el servidor lo cuenta como duplicado), y uno que se adelanta más de
    `MAX_ACK_LOOKAHEAD_FRAMES` no confirma nada: el mismo lote otra vez, y a los
    `INEXPLICABLE_ACK_ATTEMPTS` intentos se corta como el puente cierra la ventana.
    """
    stats = IngestStats(started=time.monotonic())
    first_seq = frames[0].seq
    index = 0
    lote = 0
    failing_since: float | None = None
    stalled = 0
    inexplicable = 0
    retrying = False
    expected_posts = max(1, -(-len(frames) // max_frames))
    report_every = 1 if args.verbose else max(1, expected_posts // 10)

    while index < len(frames):
        batch = frames[index : index + max_frames]
        if not retrying:
            lote += 1
        sent = batch
        if (
            args.drop_frame_every
            and not retrying
            and lote % args.drop_frame_every == 0
            and len(batch) >= 3
        ):
            sent = with_hole(batch)
            stats.holes += 1
        headers = bridge_headers(
            serial=serial,
            api_key=api_key,
            clock=clock,
            at_ms=now_ms(),
            boot_id=boot_id,
            batch=sent,
            time_sync=not args.no_time_sync,
        )
        payload = b"".join(frame.payload for frame in sent)
        posted = time.monotonic()
        response: httpx.Response | None = None
        failure = ""
        try:
            response = await client.post(
                "/ingest/ecg-frames",
                content=payload,
                headers=headers,
                timeout=args.post_timeout,
            )
        except httpx.HTTPError as error:
            failure = f"{type(error).__name__}: {error}"
        stats.latencies.append(time.monotonic() - posted)
        stats.posts += 1
        stats.bytes_sent += len(payload)
        if retrying:
            stats.retries += 1

        if response is not None and response.status_code == 202:
            failing_since, retrying = None, False
            ack = response.json()
            stats.acked += 1
            stats.duplicates += int(ack.get("framesDuplicate") or 0)
            study_id = str(ack.get("studyId"))
            if not stats.study_ids or stats.study_ids[-1] != study_id:
                stats.study_ids.append(study_id)
            last = ack.get("lastAcceptedSeq")
            # Exclusivo: la primera trama que el lote NO llevaba.
            batch_end = batch[0].seq + len(batch)
            if last is not None and int(last) + 1 - batch_end > MAX_ACK_LOOKAHEAD_FRAMES:
                # Ninguna retransmisión explica ese adelanto: el puente no
                # confirma nada (§11.6), reintenta y a la tercera cierra.
                inexplicable += 1
                stats.inexplicable += 1
                print(
                    f"  POST {stats.posts:4d} · seq {batch[0].seq}-{batch[-1].seq} · ACK "
                    f"inexplicable: lastAcceptedSeq={last} se adelanta "
                    f"{int(last) + 1 - batch_end} tramas al lote "
                    f"(intento {inexplicable}/{INEXPLICABLE_ACK_ATTEMPTS})"
                )
                if inexplicable >= INEXPLICABLE_ACK_ATTEMPTS:
                    stats.error = (
                        f"{inexplicable} ACK seguidos con lastAcceptedSeq={last} más de "
                        f"{MAX_ACK_LOOKAHEAD_FRAMES} tramas por delante del lote "
                        f"seq {batch[0].seq}-{batch[-1].seq}: el puente real cerraría la "
                        "ventana sin confirmar nada"
                    )
                    break
                retrying = True
                continue
            inexplicable = 0
            next_index = index if last is None else int(last) + 1 - first_seq
            note = ""
            if next_index > index + len(batch):
                # El servidor ya tenía más de lo que mandamos: el puente confirma
                # hasta el final del lote y nada más; lo que sigue se manda igual.
                stats.clamped += 1
                next_index = index + len(batch)
                note = f" → cursor recortado al final del lote (seq {batch_end})"
            elif next_index < index + len(batch):
                # Go-back-N: el servidor confirmó menos de lo mandado. El
                # próximo lote arranca en su cursor, no en el nuestro.
                stats.resyncs += 1
                note = f" → retoma en seq {first_seq + max(next_index, 0)}"
            if next_index <= index:
                stalled += 1
                if stalled >= MAX_STALLED_ACKS:
                    stats.error = (
                        f"{stalled} ACK seguidos sin avanzar el cursor "
                        f"(lastAcceptedSeq={last}, se esperaba ≥ {first_seq + index})"
                    )
                    break
            else:
                stalled = 0
            index = max(next_index, 0)
            if note or stats.acked % report_every == 0 or index >= len(frames):
                print(
                    f"  POST {stats.posts:4d} · seq {batch[0].seq}-{batch[-1].seq} · "
                    f"{len(sent)} tramas · {stats.latencies[-1] * 1000:6.0f} ms · "
                    f"acept {ack.get('framesAccepted')} dup {ack.get('framesDuplicate')} · "
                    f"cursor {last} ({index / len(frames):.0%}){note}"
                )
            if args.cadence and index < len(frames):
                await asyncio.sleep(args.cadence)
            continue

        if response is not None and 400 <= response.status_code < 500:
            stats.error = f"HTTP {response.status_code}: {response.text[:400]}"
            break

        # 503, otro 5xx o sin respuesta: el mismo lote otra vez.
        failing_since = failing_since or posted
        if time.monotonic() - failing_since > args.retry_window:
            stats.error = (
                f"{args.retry_window:.0f} s de fallas seguidas en seq {batch[0].seq}: "
                "el puente real cerraría la ventana acá"
            )
            break
        retrying = True
        if response is not None and response.status_code == 503:
            stats.busy += 1
            wait = _retry_after(response)
            print(f"  POST {stats.posts:4d} · 503 ocupado · reintenta en {wait:.0f} s")
        else:
            stats.server_errors += 1
            wait = 1.0
            detail = failure if response is None else f"HTTP {response.status_code}"
            if response is not None:
                detail += f": {response.text[:200]}"
            print(f"  POST {stats.posts:4d} · ✗ {detail} · reintenta")
        await asyncio.sleep(wait)

    stats.finished = time.monotonic()
    return stats


def print_ingest(stats: IngestStats, frames: list[Frame], signal_s: float) -> None:
    latencies = sorted(stats.latencies) or [0.0]
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    elapsed = max(stats.elapsed, 1e-9)
    print(
        f"  {stats.posts} POST ({stats.acked} con 202) en {stats.elapsed:.1f} s · "
        f"{len(frames) / elapsed:.0f} tramas/s · {signal_s / elapsed:.0f}× tiempo real · "
        f"{stats.bytes_sent / 1024:.0f} kB"
    )
    print(
        f"  latencia por POST: mediana {statistics.median(latencies) * 1000:.0f} ms · "
        f"p95 {p95 * 1000:.0f} ms · máx {latencies[-1] * 1000:.0f} ms"
    )
    print(
        f"  reintentos {stats.retries} (503 {stats.busy}, otros {stats.server_errors}) · "
        f"retomas go-back-N {stats.resyncs} (huecos inyectados {stats.holes}) · "
        f"tramas duplicadas {stats.duplicates}"
    )
    if stats.clamped or stats.inexplicable:
        print(
            f"  ⚠ ACK por delante del lote: {stats.clamped} recortados al final del lote, "
            f"{stats.inexplicable} inexplicables (> {MAX_ACK_LOOKAHEAD_FRAMES} tramas)"
        )
    if len(stats.study_ids) > 1:
        print(f"  ⚠ la ingesta cambió de estudio en el camino: {stats.study_ids}")


# --------------------------------------------------------------------------- #
# Cierre, espera y reporte
# --------------------------------------------------------------------------- #


async def close_study(client: httpx.AsyncClient, study_id: uuid.UUID) -> bool:
    # El chequeo de Origin de `main.py` cubre los POST con cookie: sin el header
    # pasa en desarrollo, pero en un entorno seguro daría 403.
    origin = str(settings.frontend_url).rstrip("/")
    response = await client.post(f"/studies/{study_id}/complete", headers={"Origin": origin})
    if response.status_code != 200:
        print(f"  ✗ HTTP {response.status_code}: {response.text[:400]}")
        return False
    body = response.json()
    print(
        f"  {body.get('status')} · endedAt {body.get('endedAt')} · "
        f"duración {int(body.get('durationMs') or 0) / 60000:.1f} min"
    )
    return True


@dataclass(frozen=True)
class Wait:
    progress: Progress | None
    #: Segundos hasta que terminó, se asentó o se trabó; `None` si se agotó `--timeout`.
    elapsed: float | None
    #: Estudio cerrado, nada en cola, sin lote FAILED y los cursores quietos
    #: `--stall-timeout`: una pasada del análisis falló adentro de su SAVEPOINT.
    stalled: bool = False


async def wait_for_analysis(
    factory: async_sessionmaker[AsyncSession],
    study_id: uuid.UUID,
    *,
    closed: bool,
    timeout: float,
    poll: float,
    stall_timeout: float,
) -> Wait:
    """Encuesta la base hasta que no quedan lotes en cola y los cursores llegaron.

    Con el estudio cerrado, "llegaron" es `ml_analyzed_samples` y
    `beats_analyzed_samples` ≥ `samples_count`. Con el estudio abierto eso no
    pasa nunca: la cola del tramo activo espera contexto. Ahí alcanza con que
    no quede nada en cola y los cursores dejen de moverse. Con un lote FAILED
    tampoco van a llegar nunca, así que también se corta al asentarse: esperar
    el `--timeout` entero no agrega nada.

    Tampoco llegan si falla el motor o el análisis de latidos, y eso **no** deja
    el lote FAILED: `processing._guarded` corre cada pasada en un SAVEPOINT, la
    deshace, deja `ml_analysis_failed` o `beat_analysis_failed` en el log y el
    lote queda DONE con el cursor donde estaba. Con el estudio cerrado nada lo
    reintenta hasta otro lote o un pedido del manifest, así que se corta cuando
    los cursores pasan `stall_timeout` sin moverse, sin nada en cola.
    """
    started = time.monotonic()
    last: Progress | None = None
    stable = 0
    moved_at = started
    while time.monotonic() - started < timeout:
        progress = await read_progress(factory, study_id)
        now = time.monotonic()
        elapsed = now - started
        if progress != last:
            print(f"  {elapsed:6.1f}s  {progress.line()}")
            stable = 0
            moved_at = now
        else:
            stable += 1
        last = progress
        if progress.pending == 0:
            if progress.analyzed:
                return Wait(progress, elapsed)
            settled = stable >= SETTLE_POLLS
            if settled and (not closed or progress.count(ProcessingStatus.FAILED)):
                return Wait(progress, elapsed)
            if closed and now - moved_at >= stall_timeout:
                return Wait(progress, elapsed, stalled=True)
        await asyncio.sleep(poll)
    return Wait(last, None)


def _offset_s(evidence: dict[str, Any] | None, boot_epoch_ms: int | None) -> str:
    if not evidence or boot_epoch_ms is None or evidence.get("epochMs") is None:
        return ""
    return f" @ t={(int(evidence['epochMs']) - boot_epoch_ms) / 1000:.0f}s"


def print_findings(findings: dict[str, Any]) -> None:
    quality = findings["quality"]
    print(f"  modelo: {findings.get('modelVersion')}")
    print(
        f"  calidad: analizable {quality['analyzableRatio']:.1%} · "
        f"marginal {quality['marginalRatio']:.1%} · malo {quality['badRatio']:.1%} "
        f"sobre {quality['evaluatedMs'] / 60000:.1f} min"
    )
    for interval in quality.get("intervals", []):
        print(
            f"    [{interval['startOffsetMs'] / 1000:7.1f}s → "
            f"{interval['endOffsetMs'] / 1000:7.1f}s] {interval['level']:8s} {interval['reason']}"
        )
    groups = findings.get("groups", [])
    print(f"\n  grupos de hallazgos ({len(groups)}):")
    for group in groups:
        extra = ""
        if group.get("burdenPct") is not None:
            extra = (
                f" · carga {group['burdenPct']:.2f}% "
                f"· compacidad {group.get('meanIntraCorrelation') or 0:.4f}"
            )
        print(
            f"    {group['key']:14s} {group['kind']:22s} {group['severity']:8s} "
            f"{group['occurrences']:4d} episodios · {group['beatCount']} latidos{extra}"
        )
    print(f"\n  totales: {json.dumps(findings.get('totals', {}), ensure_ascii=False)}")
    print(f"  truncado: {findings.get('truncated')}")


def print_metrics(metrics: dict[str, Any], truth: Truth, boot_epoch_ms: int | None) -> None:
    print(f"  estado: {metrics.get('status')}", end="")
    if metrics.get("unavailableReason"):
        print(f" ({metrics['unavailableReason']})", end="")
    print()
    analysis = metrics.get("analysis") or {}
    if analysis:
        print(
            f"  analizado: {int(analysis.get('analyzedMs') or 0) / 60000:.1f} min · "
            f"excluido {int(analysis.get('excludedMs') or 0) / 1000:.0f} s · "
            f"RR {analysis.get('rrIntervals')} · NN {analysis.get('nnIntervals')}"
        )

    rr = truth.rr_ms
    expected_avg = 60_000.0 / float(np.mean(rr)) if rr.size else float("nan")
    heart = metrics.get("heartRate") or {}
    if heart:
        low, high = heart.get("min") or {}, heart.get("max") or {}
        print(
            f"  FC media {heart.get('averageBpm')} lpm (escenario {expected_avg:.1f}) · "
            f"mín {low.get('value')}{_offset_s(low, boot_epoch_ms)} · "
            f"máx {high.get('value')}{_offset_s(high, boot_epoch_ms)}"
        )
        print(
            f"  latidos {heart.get('totalBeats')} (escenario {truth.rpeaks.size}, "
            f"{truth.ectopic.size} ectópicos)"
        )

    pauses = metrics.get("pauses") or {}
    if pauses:
        threshold = int(pauses.get("thresholdMs") or 0)
        expected = int(np.count_nonzero(rr >= threshold)) if threshold else 0
        longest = pauses.get("longest")
        print(
            f"  pausas ≥ {threshold} ms: {pauses.get('count')} (escenario {expected}) · "
            f"la más larga {longest.get('value') if longest else '-'}"
            f"{_offset_s(longest, boot_epoch_ms)}"
        )
        for item in (pauses.get("items") or [])[:5]:
            print(f"    {item.get('value')}{_offset_s(item, boot_epoch_ms)}")

    hrv = metrics.get("hrvTime") or {}
    if hrv:
        print(
            f"  SDNN {hrv.get('sdnnMs')} ms · rMSSD {hrv.get('rmssdMs')} ms · "
            f"pNN50 {hrv.get('pnn50Percent')} %"
        )
    for label, key in (("SV", "supraventricular"), ("V", "ventricular")):
        ectopy = metrics.get(key)
        if ectopy:
            print(f"  {label}: {ectopy.get('total')} latidos ({ectopy.get('perThousand')} ‰)")
    if metrics.get("ectopyUnavailableReason"):
        print(f"  ectopía: {metrics['ectopyUnavailableReason']}")


def print_scenario(scenario: Scenario, truth: Truth, frames: list[Frame], encode_s: float) -> None:
    payload_kb = len(frames) * FRAME_BYTES / 1024
    print(
        f"  señal: {scenario.minutes:g} min · {truth.rpeaks.size} latidos visibles "
        f"({truth.ectopic.size} ectópicos) · {len(frames)} tramas ({payload_kb:.0f} kB) · "
        f"codificada en {encode_s:.1f} s"
    )
    for start, duration, bpm in scenario.tachy:
        print(f"  taquicardia: {start:g}–{start + duration:g} s a {bpm:g} lpm")
    for at, rr_s in scenario.pauses:
        print(f"  pausa: R-R de {rr_s:g} s desde el primer latido ≥ {at:g} s")
    for start, duration in scenario.lead_off:
        kind = "sin ECG" if scenario.lead_off_flat else "solo la marca"
        print(f"  electrodo suelto: {start:g}–{start + duration:g} s ({kind})")


def dry_run(
    frames: list[Frame],
    clock: DeviceClock,
    start_ms: int,
    max_frames: int,
    args: argparse.Namespace,
) -> None:
    """Arma los lotes como si el servidor confirmara todo y muestra lo que saldría."""
    batches = [frames[i : i + max_frames] for i in range(0, len(frames), max_frames)]
    sizes = [len(batch) for batch in batches]
    seconds = [(batch[-1].end_ms - batch[0].t0_ms) / 1000 for batch in batches]
    print(
        f"▸ Dry-run: {len(batches)} POST de {min(sizes)}–{max(sizes)} tramas "
        f"({min(sizes) * FRAME_BYTES}–{max(sizes) * FRAME_BYTES} B) · "
        f"{min(seconds):.1f}–{max(seconds):.1f} s de señal por POST "
        f"(media {statistics.mean(seconds):.1f} s) · "
        f"{statistics.mean(f.n_samples for f in frames):.0f} muestras por trama"
    )
    if args.drop_frame_every:
        holes = len(batches) // args.drop_frame_every
        print(f"  huecos inyectados: ~{holes} (cada {args.drop_frame_every} POST)")
    lead_off = [i for i, batch in enumerate(batches) if any(f.lead_off for f in batch)]
    shown = sorted({0, *(lead_off[:1]), len(batches) - 1})
    for number in shown:
        batch = batches[number]
        at_ms = start_ms + int(number * args.cadence * 1000)
        headers = bridge_headers(
            serial="E2E-DRYRUN",
            api_key="<api-key>",
            clock=clock,
            at_ms=at_ms,
            boot_id=args.boot_id,
            batch=batch,
            time_sync=not args.no_time_sync,
        )
        headers["Authorization"] = "Bearer <api-key>"
        print(
            f"\n  POST {number + 1}/{len(batches)} · seq {batch[0].seq}-{batch[-1].seq} · "
            f"t0 {batch[0].t0_ms / 1000:.1f}–{batch[-1].end_ms / 1000:.1f} s · "
            f"{len(batch) * FRAME_BYTES} B"
        )
        for name, value in headers.items():
            print(f"    {name}: {value}")
    if len(frames) * FRAME_BYTES > settings.ingest_max_batch_bytes and args.single_post:
        print(f"\n  ⚠ supera ingest_max_batch_bytes ({settings.ingest_max_batch_bytes} B): 413")


# --------------------------------------------------------------------------- #
# Argumentos y orquestación
# --------------------------------------------------------------------------- #


def _numbers(text: str, count: int, label: str) -> tuple[float, ...]:
    parts = text.split(":")
    if len(parts) != count:
        raise argparse.ArgumentTypeError(f"{label}: se esperaban {count} valores separados por ':'")
    try:
        values = tuple(float(part) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{label}: '{text}' no son números") from None
    if any(value < 0 for value in values):
        raise argparse.ArgumentTypeError(f"{label}: no se aceptan valores negativos")
    return values


def tachy_arg(text: str) -> tuple[float, float, float]:
    start, duration, bpm = _numbers(text, 3, "--tachy-at INICIO:DURACIÓN:LPM")
    if duration <= 0 or not 30 <= bpm <= 250:
        raise argparse.ArgumentTypeError("--tachy-at: duración > 0 y 30 ≤ lpm ≤ 250")
    return start, duration, bpm


def pause_arg(text: str) -> tuple[float, float]:
    at, rr_s = _numbers(text, 2, "--pause-at SEGUNDO:RR_SEGUNDOS")
    if not 0 < rr_s <= 30:
        raise argparse.ArgumentTypeError("--pause-at: 0 < RR ≤ 30 s")
    return at, rr_s


def lead_off_arg(text: str) -> tuple[float, float] | None:
    if text.strip().lower() in {"-1", "none", "no"}:
        return None
    if ":" not in text:
        (start,) = _numbers(text, 1, "--lead-off-at SEGUNDO[:DURACIÓN]")
        return start, 30.0
    start, duration = _numbers(text, 2, "--lead-off-at SEGUNDO[:DURACIÓN]")
    return start, duration


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.e2e_ml",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--minutes", type=float, default=20.0, help="minutos de señal")
    parser.add_argument("--bpm", type=float, default=60.0, help="frecuencia base (lpm)")
    parser.add_argument("--seed", type=int, default=7, help="semilla del ruido")
    parser.add_argument("--noise-uv", type=float, default=8.0, help="ruido blanco, µV RMS")
    scenario = parser.add_argument_group("escenario")
    scenario.add_argument(
        "--ectopic-every", type=int, default=12, help="un ectópico cada N latidos (0 = ninguno)"
    )
    scenario.add_argument(
        "--lead-off-at",
        type=lead_off_arg,
        action="append",
        metavar="SEGUNDO[:DURACIÓN]",
        help="electrodo suelto (repetible; 30 s si no se da duración; -1 = ninguno). "
        "Por defecto 300:30",
    )
    scenario.add_argument(
        "--lead-off-flat",
        action="store_true",
        help="además de la marca, el tramo suelto queda sin ECG (solo ruido)",
    )
    scenario.add_argument(
        "--tachy-at",
        type=tachy_arg,
        action="append",
        default=[],
        metavar="INICIO:DURACIÓN:LPM",
        help="tramo de taquicardia (repetible)",
    )
    scenario.add_argument(
        "--pause-at",
        type=pause_arg,
        action="append",
        default=[],
        metavar="SEGUNDO:RR_SEGUNDOS",
        help="pausa: el R-R desde el primer latido ≥ SEGUNDO dura RR_SEGUNDOS (repetible)",
    )
    transport = parser.add_argument_group("transporte (puente ESP32-C3)")
    transport.add_argument(
        "--frames-per-post",
        type=int,
        default=BRIDGE_MAX_FRAMES,
        help=f"tramas por POST (default {BRIDGE_MAX_FRAMES}, el tope del puente)",
    )
    transport.add_argument(
        "--single-post", action="store_true", help="todo en un solo POST (comportamiento anterior)"
    )
    transport.add_argument(
        "--cadence", type=float, default=0.0, help="segundos entre POST confirmados (default 0)"
    )
    transport.add_argument(
        "--no-time-sync",
        action="store_true",
        help="sin X-Bridge-Epoch-Ms/X-Time-Sync-*: solo sirve con ingest_require_time_sync=false",
    )
    transport.add_argument("--boot-id", type=int, default=0, choices=range(16), metavar="0..15")
    transport.add_argument(
        "--drop-frame-every",
        type=int,
        default=0,
        metavar="N",
        help="omite la trama del medio en cada N-ésimo POST (solo el primer intento) para "
        "ejercitar la retransmisión go-back-N",
    )
    transport.add_argument(
        "--retry-window",
        type=float,
        default=60.0,
        help="segundos de fallas seguidas antes de cortar (el puente usa 60)",
    )
    transport.add_argument(
        "--post-timeout",
        type=float,
        default=BRIDGE_POST_TIMEOUT_S,
        help=f"segundos de espera por fase de cada POST de ingesta (default "
        f"{BRIDGE_POST_TIMEOUT_S:g}, lo que el puente espera la respuesta); pasado eso cuenta "
        "como falla y entra en la racha de --retry-window",
    )
    transport.add_argument(
        "--no-simulated",
        dest="simulated",
        action="store_false",
        help="tramas sin el bit de dato simulado, como las del puente real. Por defecto "
        "se marcan: el estudio queda is_simulated y el informe avisa SIMULATED_STUDY",
    )
    flow = parser.add_argument_group("flujo")
    flow.add_argument(
        "--seed-study",
        action="store_true",
        help="crear el estudio en la base antes de ingerir (comportamiento anterior); "
        "sin esto lo abre la ingesta, como en producción",
    )
    flow.add_argument("--no-close", action="store_true", help="no cerrar el estudio")
    flow.add_argument(
        "--timeout", type=float, default=600.0, help="segundos máximos de espera del análisis"
    )
    flow.add_argument("--poll", type=float, default=2.0, help="segundos entre encuestas")
    flow.add_argument(
        "--stall-timeout",
        type=float,
        default=90.0,
        help="con el estudio cerrado y nada en cola, segundos sin que se muevan los "
        "cursores antes de dar el análisis por trabado (default 90)",
    )
    flow.add_argument(
        "--dry-run", action="store_true", help="armar lotes y cabeceras, sin red ni base"
    )
    flow.add_argument("-v", "--verbose", action="store_true", help="una línea por POST")
    args = parser.parse_args(argv)

    if args.lead_off_at is None:
        args.lead_off_at = [(300.0, 30.0)]
    elif any(value is None for value in args.lead_off_at):
        args.lead_off_at = []
    if args.minutes <= 0:
        parser.error("--minutes tiene que ser > 0")
    if not 1 <= args.frames_per_post <= 4096:
        parser.error("--frames-per-post fuera de rango (1..4096)")
    if args.drop_frame_every < 0 or args.cadence < 0:
        parser.error("--drop-frame-every y --cadence no pueden ser negativos")
    if args.post_timeout <= 0:
        parser.error("--post-timeout tiene que ser > 0")
    if args.stall_timeout <= SETTLE_POLLS * args.poll:
        parser.error(
            f"--stall-timeout tiene que superar {SETTLE_POLLS} encuestas "
            f"({SETTLE_POLLS * args.poll:g} s con --poll {args.poll:g})"
        )
    return args


async def api_alive(api: str) -> bool:
    try:
        async with httpx.AsyncClient(base_url=api, timeout=5.0) as client:
            return (await client.get("/health/live")).status_code == 200
    except httpx.HTTPError:
        return False


async def run(args: argparse.Namespace) -> int:
    scenario = Scenario(
        minutes=args.minutes,
        bpm=args.bpm,
        ectopic_every=args.ectopic_every,
        tachy=tuple(args.tachy_at),
        pauses=tuple(args.pause_at),
        lead_off=tuple(args.lead_off_at),
        lead_off_flat=args.lead_off_flat,
        noise_uv=args.noise_uv,
        seed=args.seed,
    )

    print("▸ Generando y codificando la señal")
    encode_started = time.monotonic()
    signal_mv, flags, truth = synth_scenario(scenario)
    frames = build_frames(signal_mv, flags, args.boot_id, simulated=args.simulated)
    print_scenario(scenario, truth, frames, time.monotonic() - encode_started)
    if not frames:
        print("  ✗ la señal no produjo tramas")
        return 1
    total_samples = sum(frame.n_samples for frame in frames)
    max_frames = len(frames) if args.single_post else args.frames_per_post
    start_ms = now_ms()
    clock = DeviceClock(boot_epoch_ms=start_ms - frames[-1].end_ms - ANCHOR_MARGIN_MS)
    boot_iso = datetime.fromtimestamp(clock.boot_epoch_ms / 1000, tz=UTC).isoformat()
    print(f"  arranque del equipo (ancla): {boot_iso}")

    if args.dry_run:
        dry_run(frames, clock, start_ms, max_frames, args)
        return 0

    if not await api_alive(args.api):
        print(f"  ✗ la API no responde en {args.api}/health/live")
        return 1

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await live_run(args, factory, frames, clock, truth, total_samples, max_frames)
    finally:
        await engine.dispose()


async def live_run(
    args: argparse.Namespace,
    factory: async_sessionmaker[AsyncSession],
    frames: list[Frame],
    clock: DeviceClock,
    truth: Truth,
    total_samples: int,
    max_frames: int,
) -> int:
    print("▸ Creando médico, paciente y equipo" + (" y estudio" if args.seed_study else ""))
    seeded = await seed(
        factory,
        create_study=args.seed_study,
        started_at=datetime.fromtimestamp(clock.boot_epoch_ms / 1000, tz=UTC),
    )
    print(
        f"  equipo {seeded.serial}" + (f" · estudio {seeded.study_id}" if seeded.study_id else "")
    )

    failures: list[str] = []
    # El timeout del puente va por POST de ingesta (`send_frames`); el resto son
    # pedidos del médico.
    async with httpx.AsyncClient(base_url=args.api, timeout=API_TIMEOUT_S) as client:
        print(f"▸ Ingesta: {len(frames)} tramas en POST de hasta {max_frames}")
        stats = await send_frames(
            client,
            frames,
            serial=seeded.serial,
            api_key=seeded.api_key,
            clock=clock,
            boot_id=args.boot_id,
            max_frames=max_frames,
            args=args,
        )
        print_ingest(stats, frames, truth.duration_s)
        if stats.error:
            print(f"  ✗ ingesta cortada: {stats.error}")
            return 1
        study_id = uuid.UUID(stats.study_ids[-1]) if stats.study_ids else seeded.study_id
        if study_id is None:
            print("  ✗ ningún ACK informó el estudio")
            return 1
        print(f"  estudio {study_id}")
        if seeded.study_id is not None and seeded.study_id != study_id:
            print(f"  ⚠ la ingesta no usó el estudio creado a mano ({seeded.study_id})")
        ingest_done = time.monotonic()

        client.cookies.update(session_cookie(seeded.user))
        closed = False
        if not args.no_close:
            print(f"▸ POST /studies/{study_id}/complete")
            closed = await close_study(client, study_id)
            if not closed:
                failures.append("el cierre del estudio falló")
        close_done = time.monotonic()

        print("▸ Esperando el procesamiento (lotes y cursores del análisis)")
        wait = await wait_for_analysis(
            factory,
            study_id,
            closed=closed,
            timeout=args.timeout,
            poll=args.poll,
            stall_timeout=args.stall_timeout,
        )
        progress, waited = wait.progress, wait.elapsed
        if progress is None:
            print("  ✗ no se pudo leer el estudio")
            return 1
        analysis_done = time.monotonic()

        findings = await client.get(f"/studies/{study_id}/findings")
        metrics = await client.get(f"/studies/{study_id}/holter-metrics")

    print(f"\n▸ GET /studies/{study_id}/findings")
    if findings.status_code == 200:
        print_findings(findings.json())
    else:
        print(f"  ✗ HTTP {findings.status_code}: {findings.text[:300]}")
        failures.append("/findings no respondió 200")

    print(f"\n▸ GET /studies/{study_id}/holter-metrics")
    boot_epoch_ms = None if args.no_time_sync else clock.boot_epoch_ms
    if metrics.status_code == 200:
        print_metrics(metrics.json(), truth, boot_epoch_ms)
    else:
        print(f"  ✗ HTTP {metrics.status_code}: {metrics.text[:300]}")
        failures.append("/holter-metrics no respondió 200")

    print("\n▸ Lotes por processing_status")
    for name, count in progress.batches:
        print(f"  {name:10s} {count}")
    failed = progress.count(ProcessingStatus.FAILED)
    if failed:
        failures.append(f"{failed} lote(s) FAILED")
    if progress.pending == 0 and not failed and progress.samples != total_samples:
        failures.append(f"samples_count {progress.samples} ≠ {total_samples} enviadas")

    print("\n▸ Tiempos")
    print(f"  ingesta: {stats.elapsed:.1f} s ({stats.posts} POST)")
    if not args.no_close:
        print(f"  cierre: {close_done - ingest_done:.1f} s")
    if wait.stalled:
        stuck = [
            name
            for name, cursor in (("motor", progress.ml), ("latidos", progress.beats))
            if cursor < progress.samples
        ]
        failures.append(
            f"cursor trabado sin lote FAILED ({', '.join(stuck)}): buscar ml_analysis_failed / "
            "beat_analysis_failed / process_study_recovery_failed en los logs de la API"
        )
        print(
            f"  análisis: trabado {args.stall_timeout:.0f} s sin moverse, sin nada en cola "
            f"y sin lote FAILED · {progress.line()}"
        )
    elif waited is None:
        failures.append(f"el análisis no terminó en {args.timeout:.0f} s")
        print(f"  análisis: sin terminar a los {args.timeout:.0f} s · {progress.line()}")
    elif closed and not progress.analyzed:
        failures.append("los cursores del análisis no alcanzaron samples_count")
        print(f"  análisis: incompleto y sin lotes en cola · {progress.line()}")
    else:
        print(
            f"  análisis completo: {analysis_done - ingest_done:.1f} s desde el último ACK"
            + (f" · {analysis_done - close_done:.1f} s desde el cierre" if closed else "")
        )
        if not closed and not progress.analyzed:
            lag_ml = (progress.samples - progress.ml) / SAMPLE_RATE
            lag_beats = (progress.samples - progress.beats) / SAMPLE_RATE
            print(
                f"  estudio abierto: el motor queda {lag_ml:.0f} s detrás y los latidos "
                f"{lag_beats:.0f} s (la cola del tramo activo espera contexto)"
            )

    if failures:
        print("\n✗ " + " · ".join(failures))
        return 1
    print("\n✓ ingesta, cierre y análisis completos")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
