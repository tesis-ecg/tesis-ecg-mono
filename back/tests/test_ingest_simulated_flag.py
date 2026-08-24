"""Bit de DATO SIMULADO (`hdrFlags` bit 3).

`INTEGRACION.md` §7.3 lo lista como un "no hacer" explícito: **nunca archivar
como clínica una trama con el bit de dato simulado**. Una trama simulada es, en
todo lo demás, indistinguible de una real — mismo magic, CRC válido — así que
este bit es lo único que separa un estudio de banco de uno de un paciente.
"""

from app.db.models.study import Study
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames, post_frames


async def test_a_simulated_batch_marks_the_study(client, s3, db, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (await post_frames(client, device, api_key, build_frames(900, simulated=True))).json()

    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.is_simulated is True


async def test_a_real_batch_leaves_the_study_clinical(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (await post_frames(client, device, api_key, build_frames(900, simulated=False))).json()

    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.is_simulated is False


async def test_one_simulated_frame_contaminates_the_whole_study(
    client, s3, db, make_patient, make_device
) -> None:
    """No hay camino de vuelta: un lote limpio posterior no lo "limpia"."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (await post_frames(client, device, api_key, build_frames(900, simulated=True))).json()
    await process_batch(db, body["batchId"])

    clean = (
        await post_frames(
            client, device, api_key, build_frames(900, first_seq=500, simulated=False)
        )
    ).json()
    await process_batch(db, clean["batchId"])

    study = await db.get(Study, body["studyId"])
    assert study is not None
    await db.refresh(study)
    assert study.is_simulated is True


async def test_processing_also_sets_the_flag(client, s3, db, make_patient, make_device) -> None:
    """Doble red: lo marca la ingesta y lo vuelve a confirmar el procesamiento.

    Si alguien más adelante agrega un camino que crea batches sin pasar por el
    endpoint, el bit igual se propaga.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(900, simulated=True))).json()
    study = await db.get(Study, body["studyId"])
    assert study is not None
    study.is_simulated = False
    await db.flush()

    await process_batch(db, body["batchId"])

    await db.refresh(study)
    assert study.is_simulated is True
