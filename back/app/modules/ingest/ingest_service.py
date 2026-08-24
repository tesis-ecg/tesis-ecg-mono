"""Recepción de tramas del chaleco.

El request hace lo mínimo indispensable para poder confirmar: valida, archiva
los bytes crudos y crea el `ecg_batch`. La decodificación va después, en
background (`processing.py`) — decodificar 1,8 M de muestras de códigos de
longitud variable dentro del request dejaría al equipo esperando decenas de
segundos y haría timeout.

Ese corte no es solo una optimización: `INTEGRACION.md` §4.6 pide confirmar
**recién después de haber persistido de forma durable**, y separar "guardé tus
bytes" de "los procesé" es exactamente eso.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import structlog
from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.s3 import put_object
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.dependencies.device_dependencies import INGESTABLE_STATUSES, DeviceContext
from app.ml.decompression import SAMPLE_RATE_HZ, FrameError, FrameInfo, iter_frames, read_header
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest.ingest_schemas import IngestAckOut, IngestFramesInput

logger = structlog.get_logger(__name__)


def frames_key(study_id: uuid.UUID, first_seq: int) -> str:
    """Clave estable por lote.

    Lleva el `first_seq` y no un uuid random a propósito: si el equipo
    retransmite el mismo lote, se reescribe el mismo objeto en vez de dejar
    basura huérfana en S3.
    """
    return f"studies/{study_id}/frames/{first_seq:012d}.bin"


@dataclass(frozen=True)
class _ParsedFrame:
    info: FrameInfo
    payload: bytes


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
    )


def _parse(payload: bytes) -> tuple[list[_ParsedFrame], int]:
    """Separa las tramas utilizables de las que hay que descartar enteras.

    Una trama que no valida no se "recupera parcialmente": serían datos
    inventados presentados como señal del paciente.
    """
    try:
        raw_frames = iter_frames(payload)
    except FrameError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INGEST_BAD_BODY", "message": str(error)},
        ) from error

    parsed: list[_ParsedFrame] = []
    rejected = 0
    for raw in raw_frames:
        try:
            parsed.append(_ParsedFrame(info=read_header(raw), payload=raw))
        except FrameError:
            rejected += 1
    return parsed, rejected


def _dedupe_by_seq(frames: list[_ParsedFrame]) -> list[_ParsedFrame]:
    """Ordena por `seq` y colapsa repeticiones dentro del mismo cuerpo.

    Ordenar antes de evaluar contigüidad importa: el equipo puede retransmitir
    fuera de orden después de un corte, y una trama que llega desordenada no es
    un hueco.
    """
    by_seq: dict[int, _ParsedFrame] = {}
    for frame in frames:
        by_seq.setdefault(frame.info.seq, frame)
    return [by_seq[seq] for seq in sorted(by_seq)]


@dataclass(frozen=True)
class _AckWindow:
    already_stored: list[_ParsedFrame]
    accepted: list[_ParsedFrame]

    @property
    def total_ack(self) -> int:
        return len(self.already_stored) + len(self.accepted)


def _ack_window(frames: list[_ParsedFrame], study: Study, boot_id: int) -> _AckWindow:
    """Ventana confirmable, con la semántica go-back-N de `INTEGRACION.md` §4.6.

    Tres reglas:

    1. **Solo contiguo desde el cursor.** Si llegan la 10, la 11 y la 13 con el
       cursor en 9, se confirman 2: la 12 falta y el cursor de lectura del
       equipo no la puede saltear.
    2. **Las ya almacenadas se vuelven a confirmar.** Acá hay una diferencia
       deliberada con el canal BLE del documento, donde un duplicado no se
       re-confirma. Sobre HTTP un lote duplicado significa que *nuestra
       respuesta se perdió*, no que el equipo esté adelantado: si contestáramos
       0, el equipo reintentaría el mismo lote para siempre. Se cuentan aparte
       en `framesDuplicate` para que el reintento siga siendo visible.
    3. **El cursor puede saltar hacia adelante, nunca hacia atrás.** Un reinicio
       del equipo pone `t0Ms` en cero pero **no rebobina `seq`**: §4.3 habla de
       "dos tramas *consecutivas por seq* con bootId distinto". Lo que el equipo
       no alcanzó a mandar del boot anterior se perdió con él, así que ante un
       `bootId` nuevo el cursor avanza hasta donde arranque el lote en vez de
       esperar para siempre un hueco que nadie va a llenar (§4.6: los huecos que
       no se llenan son pérdida real de señal).

       Lo que ya no se acepta es una `seq` **anterior** al cursor bajo otro
       `bootId`. Antes se aceptaba: el cursor se descartaba entero ante cualquier
       cambio de boot y el lote entraba desde su primera trama. Como los objetos
       del estudio se nombran con el `first_seq` del lote (`frames_key`,
       `segment_key`, `envelope_key`), eso **sobreescribía en S3** señal ya
       archivada mientras `samples_count` seguía creciendo: el estudio perdía
       muestras en silencio y quedaba contando las que ya no estaban.
    """
    cursor = study.last_ingested_seq
    rebooted = study.last_boot_id is not None and study.last_boot_id != boot_id

    if cursor is None:
        already: list[_ParsedFrame] = []
        fresh = frames
        expected = frames[0].info.seq if frames else 0
    else:
        already = [f for f in frames if f.info.seq <= cursor]
        fresh = [f for f in frames if f.info.seq > cursor]
        expected = fresh[0].info.seq if (rebooted and fresh) else cursor + 1

    accepted: list[_ParsedFrame] = []
    for frame in fresh:
        if frame.info.seq != expected:
            break  # hueco: se corta acá y el resto espera a que el equipo lo llene
        accepted.append(frame)
        expected += 1

    return _AckWindow(already_stored=already, accepted=accepted)


async def _resolve_study(
    db: AsyncSession, ctx: DeviceContext, first: FrameInfo, epoch_anchor_ms: int
) -> tuple[Study, Patient]:
    """`serial → device.patient_id → estudio in_progress`, creándolo si no hay.

    Es el flujo real: el chaleco se enciende y empieza a grabar; nadie abre un
    estudio a mano antes.

    Devuelve también el paciente porque el caller le actualiza el estado de
    seguimiento: es la única parte del sistema que sabe que llegó señal.
    """
    device = ctx.device
    if device.patient_id is None:
        raise _conflict(
            "DEVICE_UNASSIGNED",
            "El dispositivo no tiene un paciente asignado.",
        )
    patient = await repo.get_active_patient(db, device.patient_id)
    if patient is None:
        raise _conflict("PATIENT_NOT_FOUND", "El paciente asignado no está activo.")

    study = await repo.get_open_study_for_update(db, patient.id, device.id)
    if study is not None:
        return study, patient

    started_at = datetime.fromtimestamp((epoch_anchor_ms + first.t0_ms) / 1000, tz=UTC)
    study = await repo.create_study(
        db,
        patient_id=patient.id,
        device_id=device.id,
        started_at=started_at,
        sample_rate=SAMPLE_RATE_HZ,
    )
    return study, patient


async def ingest_frames(
    ctx: DeviceContext,
    input_data: IngestFramesInput,
    db: AsyncSession,
    background: BackgroundTasks | None = None,
) -> IngestAckOut:
    if len(input_data.payload) > settings.ingest_max_batch_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "INGEST_BATCH_TOO_LARGE",
                "message": (
                    f"El lote supera {settings.ingest_max_batch_bytes} bytes. Enviarlo en partes."
                ),
            },
        )

    parsed, rejected = _parse(input_data.payload)
    received = len(input_data.payload) // 256

    epoch_anchor_ms = int(input_data.received_at.timestamp() * 1000) - ctx.uptime_ms

    if not parsed:
        # Todas las tramas fallaron la validación. Igual hay que resolver el
        # estudio para poder reportar el hueco contra algo.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INGEST_NO_VALID_FRAMES",
                "message": f"Ninguna de las {received} tramas pasó la validación.",
            },
        )

    # El filtro por bootId va ANTES de deduplicar: un lote puede cruzar un
    # reinicio del equipo, y dos tramas de boots distintos pueden compartir
    # `seq`. Deduplicar primero haría desaparecer una de las dos en silencio.
    # Solo se procesa el tramo del primer bootId; el resto llega en el request
    # siguiente, con su propia ancla temporal — mezclarlos bajo una sola ancla
    # daría horas de pared incorrectas.
    boot_id = parsed[0].info.boot_id
    frames = _dedupe_by_seq([f for f in parsed if f.info.boot_id == boot_id])

    # La autenticación leyó el equipo sin lock. Se vuelve a cargar dentro de la
    # transacción del caso de uso para competir de forma segura con otra ingesta
    # y con assign/unassign/reassign, que bloquean la misma fila primero.
    locked_device = await repo.get_device_for_update(db, ctx.device.id)
    if locked_device is None:
        raise _conflict("DEVICE_NOT_INGESTABLE", "El dispositivo ya no está disponible.")
    if locked_device.status not in INGESTABLE_STATUSES:
        raise _conflict(
            "DEVICE_NOT_INGESTABLE",
            f"El dispositivo está en estado '{locked_device.status.value}'.",
        )
    ctx = replace(ctx, device=locked_device)

    study, patient = await _resolve_study(db, ctx, frames[0].info, epoch_anchor_ms)
    window = _ack_window(frames, study, boot_id)

    ctx.device.last_seen_at = input_data.received_at
    if ctx.battery_pct is not None:
        ctx.device.last_battery_pct = ctx.battery_pct
    if ctx.firmware_version:
        ctx.device.firmware_version = ctx.firmware_version

    # El paciente también tiene telemetría, y hasta acá nadie la escribía: su
    # `study_status` se quedaba en el valor del alta y `last_data_received_at`
    # en NULL para siempre. El dashboard cuenta pacientes por ese campo, así que
    # sin esto el KPI "Pacientes activos" daba 0 con estudios corriendo.
    #
    # Se escribe con cualquier lote de tramas válidas, incluso si todas resultan
    # duplicadas: que el equipo esté retransmitiendo también es señal de vida.
    patient.last_data_received_at = input_data.received_at
    if study.status is StudyStatus.IN_PROGRESS:
        patient.study_status = PatientStudyStatus.ACTIVE

    batch_id: uuid.UUID | None = None
    if window.accepted:
        batch_id = await _store_batch(db, ctx, input_data, study, window, boot_id, epoch_anchor_ms)
        if background is not None:
            from app.modules.ingest.processing import process_batch_task

            background.add_task(process_batch_task, batch_id)

    if frames[0].info.simulated and not study.is_simulated:
        # Una sola trama de banco contamina el estudio entero: no se puede
        # archivar como clínico (INTEGRACION.md §7.3). No hay camino de vuelta.
        study.is_simulated = True

    await db.commit()

    await logger.ainfo(
        "ingest_batch_received",
        serial=ctx.device.serial_number,
        study_id=str(study.id),
        received=received,
        accepted=len(window.accepted),
        duplicate=len(window.already_stored),
        rejected=rejected,
    )

    last_seq = window.accepted[-1].info.seq if window.accepted else study.last_ingested_seq
    return IngestAckOut(
        framesReceived=received,
        framesAccepted=window.total_ack,
        framesRejected=rejected,
        framesDuplicate=len(window.already_stored),
        lastAcceptedSeq=last_seq,
        batchId=batch_id,
        studyId=study.id,
        serverTime=datetime.now(UTC),
    )


async def _store_batch(
    db: AsyncSession,
    ctx: DeviceContext,
    input_data: IngestFramesInput,
    study: Study,
    window: _AckWindow,
    boot_id: int,
    epoch_anchor_ms: int,
) -> uuid.UUID:
    """Archiva los bytes crudos y deja el lote listo para procesar.

    Solo se archiva el tramo **aceptado**. Las tramas posteriores a un hueco no
    se guardan porque el equipo las va a retransmitir igual (go-back-N reenvía
    desde la más vieja sin confirmar): guardarlas sería duplicar trabajo y dejar
    objetos que después hay que reconciliar.
    """
    accepted = window.accepted
    first_info = accepted[0].info
    last_info = accepted[-1].info

    key = frames_key(study.id, first_info.seq)
    body = b"".join(frame.payload for frame in accepted)
    put_object(key, body)

    n_samples = sum(frame.info.n_samples for frame in accepted)
    duration_ms = (last_info.t0_ms + last_info.duration_ms) - first_info.t0_ms

    batch = ECGBatch(
        device_id=ctx.device.id,
        study_id=study.id,
        received_at=input_data.received_at,
        batch_timestamp=(epoch_anchor_ms + first_info.t0_ms) // 1000,
        duration_seconds=max(duration_ms // 1000, 0),
        sample_rate=SAMPLE_RATE_HZ,
        num_channels=first_info.n_channels,
        num_samples=n_samples,
        compression_type="rice-frame-v1",
        s3_key=key,
        frames_s3_key=key,
        file_size_bytes=len(body),
        processing_status=ProcessingStatus.PENDING,
        firmware_version=ctx.firmware_version or ctx.device.firmware_version,
        boot_id=boot_id,
        device_uptime_ms=ctx.uptime_ms,
        epoch_anchor_ms=epoch_anchor_ms,
        first_seq=first_info.seq,
        last_seq=last_info.seq,
        frames_count=len(accepted),
        frames_rejected=0,
        frames_duplicate=len(window.already_stored),
    )
    await repo.create_batch(db, batch)

    study.last_ingested_seq = last_info.seq
    study.last_boot_id = boot_id
    return batch.id
