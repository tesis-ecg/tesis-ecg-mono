"""Reconstrucción temporal: de `millis()` a hora de pared.

El equipo no tiene reloj de tiempo real. Todos los timestamps son milisegundos
desde el arranque, y la conversión a UTC es responsabilidad del backend
(`INTEGRACION.md` §5). Tres cosas rompen el ancla y las tres se prueban acá.
"""

from sqlalchemy import select

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch
from app.db.models.study import Study, StudyStatus
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


async def test_a_rewound_seq_that_is_not_archived_opens_a_new_study(
    client, s3, db, make_patient, make_device
) -> None:
    """El modo de falla más caro de la integración, cerrado (`INTEGRACION.md` §11.6).

    Los cursores del log viven en la metadata de la flash. Al actualizar el
    firmware cambia el formato de esa metadata y el equipo arranca `writeSeq_` en
    0, así que sus tramas caen enteras por debajo de nuestro cursor.

    Tres comportamientos distintos, en orden histórico:

    1. Se contestaba como duplicado: se re-confirmaba el cursor viejo y el equipo
       borraba de su flash un lote que **nunca se archivó**. Pérdida permanente y
       silenciosa.
    2. Se contestaba `409 STUDY_SEQ_REWIND`: no se perdía señal, pero el equipo
       reintentaba el mismo lote para siempre y el estudio no volvía a avanzar
       solo. Si el corte duraba lo suficiente, el backlog daba la vuelta y ahí sí
       se perdía registro.
    3. **Hoy**: se cierra el estudio viejo, se abre uno nuevo y la señal entra.
       Queda una alerta para que el médico sepa por qué hay dos estudios.
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

    assert response.status_code == 202
    body = response.json()
    # La señal nueva entró, y entró en un estudio distinto.
    assert body["studyId"] != first["studyId"]
    assert body["framesAccepted"] > 0
    assert body["lastAcceptedSeq"] == 0 + body["framesAccepted"] - 1

    # El estudio viejo quedó cerrado, con su cursor y su señal intactos: nada de
    # lo ya archivado se pisó ni se perdió.
    old = await db.get(Study, first["studyId"])
    assert old is not None
    assert old.status is StudyStatus.COMPLETED
    assert old.ended_at is not None
    assert old.last_ingested_seq == cursor

    new = await db.get(Study, body["studyId"])
    assert new is not None
    assert new.status is StudyStatus.IN_PROGRESS
    assert new.last_boot_id == 4

    alert = (
        await db.execute(
            select(Alert).where(Alert.patient_id == patient.id, Alert.kind == "study_seq_rewind")
        )
    ).scalar_one()
    assert alert.severity is AlertSeverity.HIGH


async def test_a_rewound_batch_that_crosses_the_cursor_opens_a_new_study(
    client, s3, db, make_patient, make_device
) -> None:
    """El prefijo rebobinado no puede confirmarse solo porque el lote sigue.

    Es el caso que faltaba: el primer POST después de actualizar firmware puede
    traer `seq=0` hasta una secuencia mayor que el cursor del estudio anterior.
    Antes el detector miraba solamente la última trama, dejaba pasar el lote y
    el ACK confirmaba las tramas nuevas bajo el cursor como si fueran duplicadas.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    first = (
        await post_frames(client, device, api_key, build_frames(1_500, boot_id=3, first_seq=1))
    ).json()
    cursor = first["lastAcceptedSeq"]

    # El 0 no existe en el estudio anterior; el lote nuevo lo incluye y además
    # avanza más allá de su cursor.
    rewound = build_frames(9_000, boot_id=4, first_seq=0, t0_ms=0)
    assert len(rewound) > cursor
    second = (await post_frames(client, device, api_key, rewound)).json()

    assert second["studyId"] != first["studyId"]
    assert second["framesAccepted"] == len(rewound)
    assert second["framesDuplicate"] == 0
    assert second["lastAcceptedSeq"] == len(rewound) - 1

    batch = await db.get(ECGBatch, second["batchId"])
    assert batch is not None
    assert batch.frames_count == len(rewound)
    assert batch.first_seq == 0
    assert batch.last_seq == len(rewound) - 1


async def test_a_crossing_retransmission_with_an_archived_prefix_stays_in_the_study(
    client, s3, db, make_patient, make_device
) -> None:
    """El prefijo ya archivado se confirma aunque el lote también traiga señal nueva."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    archived = build_frames(3_000, boot_id=3, first_seq=0)
    first = (await post_frames(client, device, api_key, archived)).json()

    crossing = build_frames(9_000, boot_id=4, first_seq=0, t0_ms=0)
    assert len(crossing) > len(archived)
    second = (await post_frames(client, device, api_key, crossing)).json()

    assert second["studyId"] == first["studyId"]
    assert second["framesDuplicate"] == len(archived)
    assert second["framesAccepted"] == len(crossing)
    assert second["lastAcceptedSeq"] == len(crossing) - 1

    batch = await db.get(ECGBatch, second["batchId"])
    assert batch is not None
    assert batch.first_seq == len(archived)
    assert batch.frames_count == len(crossing) - len(archived)


async def test_a_rewind_does_not_open_a_study_per_post(
    client, s3, db, make_patient, make_device
) -> None:
    """La recuperación corre UNA vez, no en cada reintento.

    Sin esta guarda, un equipo que quedara rebobinado abriría un estudio nuevo
    cada diez minutos y el paciente terminaría con doscientos estudios de un
    lote. Lo que la evita es que el estudio recién creado arranca con
    `last_ingested_seq = None`, y sin cursor no hay contra qué rebobinar.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    await post_frames(client, device, api_key, build_frames(1500, boot_id=3, first_seq=800))
    frames = build_frames(3000, boot_id=4, first_seq=0, t0_ms=0)

    second = (await post_frames(client, device, api_key, frames[:2])).json()
    third = (await post_frames(client, device, api_key, frames[2:4])).json()

    assert third["studyId"] == second["studyId"]
    studies = (
        (await db.execute(select(Study).where(Study.patient_id == patient.id))).scalars().all()
    )
    assert len(studies) == 2


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
