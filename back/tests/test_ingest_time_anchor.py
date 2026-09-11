"""Reconstrucción temporal: de `millis()` a hora de pared.

El equipo no tiene reloj de tiempo real. Todos los timestamps son milisegundos
desde el arranque, y la conversión a UTC es responsabilidad del backend
(`INTEGRACION.md` §5). Tres cosas rompen el ancla y las tres se prueban acá.
"""

from sqlalchemy import select

from app.db.models.ecg_batch import ECGBatch
from app.db.models.study import Study
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames, now_ms, post_frames


async def _batches(db) -> list[ECGBatch]:
    return list((await db.scalars(select(ECGBatch).order_by(ECGBatch.created_at))).all())


async def test_anchor_is_the_bridge_epoch_minus_uptime(
    client, s3, db, make_patient, make_device
) -> None:
    """El arranque del equipo sale de la pareja que manda el puente.

    El epoch y el uptime describen el mismo instante, así que su resta da el
    momento en que el equipo se prendió sin que la latencia del pedido entre en
    la cuenta.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    uptime_ms = 5_400_000
    bridge_epoch_ms = now_ms()

    body = (
        await post_frames(
            client,
            device,
            api_key,
            build_frames(900),
            uptime_ms=uptime_ms,
            bridge_epoch_ms=bridge_epoch_ms,
        )
    ).json()

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.epoch_anchor_ms == bridge_epoch_ms - uptime_ms


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


async def test_a_rewound_seq_that_is_not_archived_is_rejected_loudly(
    client, s3, db, make_patient, make_device
) -> None:
    """El modo de falla más caro de la integración, cerrado (`INTEGRACION.md` §11.6).

    Los cursores del log viven en la metadata de la flash. Al actualizar el
    firmware cambia el formato de esa metadata y el equipo arranca `writeSeq_` en
    0, así que sus tramas caen enteras por debajo de nuestro cursor.

    Antes eso se contestaba como duplicado: se re-confirmaba el cursor viejo y el
    equipo borraba de su flash un lote que **nunca se archivó**. Pérdida
    permanente y silenciosa de todo el estudio nuevo — el equipo creía que llegó
    y nosotros que era una repetición.

    Ahora falla con un código propio. El estudio en curso hay que cerrarlo antes
    de que este equipo pueda volver a subir, que es la regla operativa que ya
    pedía el documento.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    first = (
        await post_frames(client, device, api_key, build_frames(1500, boot_id=3, first_seq=800))
    ).json()
    cursor = first["lastAcceptedSeq"]

    response = await post_frames(
        client, device, api_key, build_frames(1500, boot_id=4, first_seq=0, t0_ms=0)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "STUDY_SEQ_REWIND"
    # El cursor no se movió: lo ya archivado sigue siendo lo único confirmado.
    study = await db.get(Study, first["studyId"])
    assert study is not None
    assert study.last_ingested_seq == cursor


async def test_a_rewound_seq_that_IS_archived_is_still_a_duplicate(
    client, s3, db, make_patient, make_device
) -> None:
    """La retransmisión legítima bajo otro `bootId` se sigue re-confirmando.

    Es el caso que hay que no romper al cerrar el de arriba: mirando solo los
    números los dos son idénticos. Lo que los separa es si las tramas están
    archivadas, y acá lo están, así que el ACK las vuelve a confirmar para que el
    equipo no reintente el mismo lote para siempre.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(1800, boot_id=2, first_seq=0)

    first = (await post_frames(client, device, api_key, frames)).json()

    replay = build_frames(1800, boot_id=5, first_seq=0, t0_ms=0)
    second = (await post_frames(client, device, api_key, replay)).json()

    assert second["framesDuplicate"] == len(replay)
    assert second["batchId"] is None  # nada nuevo se archivó
    assert second["lastAcceptedSeq"] == first["lastAcceptedSeq"]


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
