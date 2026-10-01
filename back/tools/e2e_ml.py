"""Verificación e2e del motor de detección contra el stack real.

Recorre el camino de producción completo, sin mocks:

    ECG sintético con foco ectópico
      → codificado a tramas Rice de 256 B (el MISMO codec del firmware)
      → POST /ingest/ecg-frames con el bearer del equipo
      → process_batch: decodifica, archiva en MinIO, corre el motor
      → GET /studies/{id}/findings

Es la contraparte scripteable del simulador de chaleco del portal: genera
exactamente el mismo tipo de señal y usa el mismo codec, pero se puede correr
desde la terminal y comprobar los números.

    uv run python -m tools.e2e_ml

No es un test de CI: necesita la base, MinIO y la API levantados. Vive en
`tools/` y no en `app/` justamente por eso.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.models.device import Device, DeviceStatus  # noqa: E402
from app.db.models.doctor import Doctor  # noqa: E402
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus  # noqa: E402
from app.db.models.study import Study, StudyStatus  # noqa: E402
from app.db.models.user import IdentityStatus, User, UserRole  # noqa: E402
from app.ml.decompression import STEP_MS  # noqa: E402
from tests.ecg_synth import SAMPLE_RATE, synth_ecg, to_microvolts  # noqa: E402
from tests.frame_builder import Sample, encode_samples  # noqa: E402


async def seed(minutes: int) -> tuple[uuid.UUID, str, str, User]:
    """Crea médico, paciente, equipo y estudio. Devuelve lo que hace falta para ingerir."""
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
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
            firmware_version="1.4.2",
        )
        db.add(device)
        await db.flush()
        study = Study(
            patient_id=patient.id,
            device_id=device.id,
            started_at=datetime.now(UTC) - timedelta(minutes=minutes + 5),
            status=StudyStatus.IN_PROGRESS,
            sample_rate=SAMPLE_RATE,
        )
        db.add(study)
        await db.commit()
        await engine.dispose()
        return study.id, device.serial_number, api_key, user


def build_frames(minutes: int, ectopic_every: int, lead_off_at: int | None) -> bytes:
    signal = synth_ecg(duration_s=minutes * 60.0, ectopic_every=ectopic_every)
    flags = signal.flags.copy()
    if lead_off_at is not None:
        from app.ml.decompression import FLAG_LEAD_OFF

        start = lead_off_at * SAMPLE_RATE
        flags[start : start + 30 * SAMPLE_RATE] |= FLAG_LEAD_OFF
    samples = [
        Sample(timestamp_ms=index * STEP_MS, raw_uV=[value], flags=int(flag))
        for index, (value, flag) in enumerate(
            zip(to_microvolts(signal.signal_mv), flags, strict=True)
        )
    ]
    frames = encode_samples(samples, first_seq=0, boot_id=0, simulated=True)
    print(
        f"  señal: {minutes} min · {len(signal.rpeaks)} latidos "
        f"({len(signal.ectopic_peaks)} ectópicos) → {len(frames)} tramas "
        f"({len(frames) * 256 / 1024:.0f} kB)"
    )
    return b"".join(frames)


def session_cookie(user: User) -> dict[str, str]:
    """Cookie de sesión firmada con el mismo secreto que usa la API.

    Se emite acá y no se pide por `/auth/login` porque ese camino pasa por
    Auth0, y el punto de este script es no depender de nada externo.
    """
    from app.core.security import create_access_token

    token, _ = create_access_token(user)
    return {"holter_session_v2": token}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--minutes", type=int, default=20)
    parser.add_argument("--ectopic-every", type=int, default=12)
    parser.add_argument("--lead-off-at", type=int, default=300, help="segundo, o -1 para ninguno")
    args = parser.parse_args()

    print("▸ Creando médico, paciente, equipo y estudio")
    study_id, serial, api_key, user = await seed(args.minutes)
    print(f"  estudio {study_id}")

    print("▸ Generando y codificando la señal")
    payload = build_frames(
        args.minutes, args.ectopic_every, None if args.lead_off_at < 0 else args.lead_off_at
    )

    async with httpx.AsyncClient(base_url=args.api, timeout=300.0) as client:
        print("▸ POST /ingest/ecg-frames")
        started = datetime.now(UTC)
        response = await client.post(
            "/ingest/ecg-frames",
            content=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Device-Serial": serial,
                "X-Device-Uptime-Ms": str(args.minutes * 60_000),
                "Content-Type": "application/octet-stream",
                "X-Firmware-Version": "1.4.2",
                "X-Battery-Pct": "87",
            },
        )
        if response.status_code != 202:
            print(f"  ✗ {response.status_code}: {response.text[:400]}")
            return 1
        body = response.json()
        print(f"  respuesta: {json.dumps(body, ensure_ascii=False)[:300]}")

        print("▸ Esperando al procesamiento en background")
        client.cookies.update(session_cookie(user))
        findings: dict[str, object] = {}
        for _ in range(120):
            await asyncio.sleep(1.0)
            result = await client.get(f"/studies/{study_id}/findings")
            if result.status_code != 200:
                continue
            findings = result.json()
            if findings.get("groups") or findings["quality"]["intervals"]:
                break
        elapsed = (datetime.now(UTC) - started).total_seconds()

    if not findings:
        print("  ✗ el endpoint nunca devolvió hallazgos")
        return 1

    print(f"\n▸ GET /studies/{study_id}/findings   ({elapsed:.1f} s desde la ingesta)")
    quality = findings["quality"]
    print(f"  modelo: {findings['modelVersion']}")
    print(
        f"  calidad: analizable {quality['analyzableRatio']:.1%} · "
        f"marginal {quality['marginalRatio']:.1%} · malo {quality['badRatio']:.1%} "
        f"sobre {quality['evaluatedMs'] / 60000:.1f} min"
    )
    for interval in quality["intervals"]:
        print(
            f"    [{interval['startOffsetMs'] / 1000:7.1f}s → "
            f"{interval['endOffsetMs'] / 1000:7.1f}s] {interval['level']:8s} {interval['reason']}"
        )

    print(f"\n  grupos de hallazgos ({len(findings['groups'])}):")
    for group in findings["groups"]:
        extra = ""
        if group["burdenPct"] is not None:
            extra = (
                f" · carga {group['burdenPct']:.2f}% "
                f"· compacidad {group['meanIntraCorrelation']:.4f}"
            )
        print(
            f"    {group['key']:14s} {group['kind']:22s} {group['severity']:8s} "
            f"{group['occurrences']:4d} episodios · {group['beatCount']} latidos{extra}"
        )
    print(f"\n  totales: {json.dumps(findings['totals'], ensure_ascii=False)}")
    print(f"  truncado: {findings['truncated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
