"""Reconstrucción temporal: de `millis()` a hora de pared.

El equipo no tiene reloj de tiempo real. Todos los timestamps son milisegundos
desde el arranque, y la conversión a UTC es responsabilidad del backend
(`INTEGRACION.md` §5). Tres cosas rompen el ancla y las tres se prueban acá.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models.ecg_batch import ECGBatch
from app.db.models.study import Study
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames, post_frames


async def _batches(db) -> list[ECGBatch]:
    return list((await db.scalars(select(ECGBatch).order_by(ECGBatch.created_at))).all())


async def test_anchor_is_reception_time_minus_uptime(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    uptime_ms = 5_400_000

    before = int(datetime.now(UTC).timestamp() * 1000)
    body = (
        await post_frames(client, device, api_key, build_frames(900), uptime_ms=uptime_ms)
    ).json()
    after = int(datetime.now(UTC).timestamp() * 1000)

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.epoch_anchor_ms is not None
    assert before - uptime_ms <= batch.epoch_anchor_ms <= after - uptime_ms


async def test_raw_uptime_and_boot_id_are_persisted_next_to_the_derived_time(
    client, s3, db, make_patient, make_device
) -> None:
    """Con el crudo se puede recalcular todo; con el UTC ya derivado, no."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (
        await post_frames(client, device, api_key, build_frames(900, boot_id=7), uptime_ms=999_000)
    ).json()

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.device_uptime_ms == 999_000
    assert batch.boot_id == 7
    assert batch.epoch_anchor_ms is not None
    assert batch.received_at is not None


async def test_a_reboot_lets_the_cursor_jump_forward_over_the_lost_frames(
    client, s3, db, make_patient, make_device
) -> None:
    """`bootId` distinto = el equipo se reinició y `t0Ms` volvió a cero.

    Lo que el equipo tenía sin mandar del boot anterior se perdió con el corte.
    El cursor salta hasta donde arranca el lote nuevo en vez de esperar para
    siempre un hueco que nadie va a llenar (`INTEGRACION.md` §4.6).
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    first = (
        await post_frames(client, device, api_key, build_frames(1500, boot_id=3, first_seq=800))
    ).json()
    assert first["framesAccepted"] > 0

    # Reinicio: bootId nuevo, y la seq sigue corriendo pero con un hueco.
    resume_at = first["lastAcceptedSeq"] + 50
    second = (
        await post_frames(
            client, device, api_key, build_frames(1500, boot_id=4, first_seq=resume_at, t0_ms=0)
        )
    ).json()

    assert second["framesAccepted"] > 0
    assert second["studyId"] == first["studyId"]
    study = await db.get(Study, second["studyId"])
    assert study is not None
    assert study.last_boot_id == 4


async def test_a_reboot_does_not_let_the_cursor_go_backwards(
    client, s3, db, make_patient, make_device
) -> None:
    """La `seq` no rebobina en un reinicio: §4.3 la describe como continua entre boots.

    Un lote que arranca antes del cursor con otro `bootId` solo puede ser un
    error, y aceptarlo sobreescribiría en S3 los objetos del estudio, que se
    nombran con el `first_seq` del lote.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    first = (
        await post_frames(client, device, api_key, build_frames(1500, boot_id=3, first_seq=800))
    ).json()
    cursor = first["lastAcceptedSeq"]

    second = (
        await post_frames(
            client, device, api_key, build_frames(1500, boot_id=4, first_seq=0, t0_ms=0)
        )
    ).json()

    assert second["framesAccepted"] > 0  # se re-confirman, para que el equipo no cicle
    assert second["framesDuplicate"] == second["framesAccepted"]
    assert second["batchId"] is None  # nada nuevo se archivó
    assert second["lastAcceptedSeq"] == cursor
    study = await db.get(Study, second["studyId"])
    assert study is not None
    assert study.last_ingested_seq == cursor


async def test_boot_id_wrapping_from_15_to_0_counts_as_a_change(
    client, s3, db, make_patient, make_device
) -> None:
    """El bootId son 4 bits y da la vuelta. 15 → 0 es un reinicio, no una igualdad."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    first = (
        await post_frames(client, device, api_key, build_frames(1500, boot_id=15, first_seq=900))
    ).json()
    body = (
        await post_frames(
            client,
            device,
            api_key,
            build_frames(1500, boot_id=0, first_seq=first["lastAcceptedSeq"] + 30),
        )
    ).json()

    assert body["framesAccepted"] > 0
    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.last_boot_id == 0


async def test_millis_wraparound_does_not_produce_a_negative_gap(
    client, s3, db, make_patient, make_device
) -> None:
    """A los 49,7 días `t0Ms` retrocede SIN que cambie el bootId.

    En un estudio de 15 o 30 días es cuestión de tiempo. La duración del estudio
    no puede quedar negativa por eso.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    near_wrap = 0xFFFFFFFF - 5_000
    first = (
        await post_frames(client, device, api_key, build_frames(1500, boot_id=2, t0_ms=near_wrap))
    ).json()
    await process_batch(db, first["batchId"])

    after_wrap = (
        await post_frames(
            client,
            device,
            api_key,
            build_frames(1500, boot_id=2, first_seq=500, t0_ms=0),
        )
    ).json()
    await process_batch(db, after_wrap["batchId"])

    study = await db.get(Study, first["studyId"])
    assert study is not None
    await db.refresh(study)
    assert (study.duration_ms or 0) >= 0


async def test_a_batch_spanning_two_boots_only_takes_the_first(
    client, s3, db, make_patient, make_device
) -> None:
    """Un lote puede cruzar un reinicio si el equipo drena backlog viejo.

    Las tramas del boot siguiente esperan al request siguiente, que trae su
    propia ancla temporal — mezclarlas bajo una sola ancla daría horas mal.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    boot_a = build_frames(900, boot_id=1, first_seq=0)
    boot_b = build_frames(900, boot_id=2, first_seq=0, t0_ms=0)

    body = (await post_frames(client, device, api_key, boot_a + boot_b)).json()

    assert body["framesAccepted"] == len(boot_a)
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.boot_id == 1
