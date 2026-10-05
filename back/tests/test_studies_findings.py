"""`GET /studies/{id}/findings` — el contrato que consume el panel del médico."""

import numpy as np
import pytest
from sqlalchemy import select

from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.signal_quality import SignalQualityInterval
from app.db.models.user import UserRole
from app.ml.decompression import FLAG_LEAD_OFF
from app.ml.pipeline import PIPELINE_VERSION
from app.modules.ingest.processing import process_batch
from tests.ecg_synth import SAMPLE_RATE, synth_ecg, to_microvolts
from tests.frame_builder import Sample, encode_samples
from tests.ingest_helpers import STEP_MS, post_frames

pytestmark = pytest.mark.usefixtures("ml_engine")


def _frames(signal_mv: np.ndarray, flags: np.ndarray) -> list[bytes]:
    samples = [
        Sample(timestamp_ms=index * STEP_MS, raw_uV=[value], flags=int(flag))
        for index, (value, flag) in enumerate(zip(to_microvolts(signal_mv), flags, strict=True))
    ]
    return encode_samples(samples, first_seq=0, boot_id=0, simulated=True)


async def _study_with_ectopics(client, db, make_patient, make_device, *, lead_off: bool = False):
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    # Tres bloques de 300 s, más el contexto derecho que el tercero espera con
    # la corrida abierta (`ml_analysis_lookahead_seconds`).
    signal = synth_ecg(duration_s=930.0, ectopic_every=12)
    flags = signal.flags.copy()
    if lead_off:
        flags[300 * SAMPLE_RATE : 340 * SAMPLE_RATE] |= FLAG_LEAD_OFF
    body = (await post_frames(client, device, api_key, _frames(signal.signal_mv, flags))).json()
    await process_batch(db, body["batchId"])
    return body["studyId"], signal


async def test_los_episodios_de_una_morfologia_vienen_agrupados_bajo_su_cluster(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """412 latidos de la misma forma son UN hallazgo con 412 ocurrencias.

    Es la diferencia entre un panel que un médico lee en media hora y una lista
    de cuatrocientas filas que nadie mira.
    """
    study_id, signal = await _study_with_ectopics(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/studies/{study_id}/findings")
    assert response.status_code == 200, response.text
    body = response.json()

    clusters = [group for group in body["groups"] if group["key"].startswith("cluster:")]
    assert len(clusters) == 1
    grupo = clusters[0]
    assert grupo["kind"] == "recurrent_morphology"
    assert grupo["category"] == "clinical", "ANOMALY tiene que caer en clínico, no en técnico"
    assert grupo["beatCount"] == pytest.approx(len(signal.ectopic_peaks), rel=0.1)
    assert grupo["burdenPct"] == pytest.approx(100 / 12, rel=0.2)
    assert grupo["meanIntraCorrelation"] > 0.95
    assert grupo["occurrences"] > 0
    assert grupo["items"], "el grupo trae ejemplos navegables"
    assert all(item["kind"] == "morphology_anomaly" for item in grupo["items"])
    assert body["modelVersion"] == PIPELINE_VERSION


async def test_el_encabezado_de_morfologia_no_se_dibuja_sobre_la_traza(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Abarca del primer al último latido de su cluster —horas—: pintarlo sería
    una banda sobre todo el ECG. Va en `/findings`, no en el manifest."""
    study_id, _ = await _study_with_ectopics(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    kinds = {item["kind"] for item in manifest["annotations"]}
    assert "morphology_anomaly" in kinds
    assert "recurrent_morphology" not in kinds

    findings = (await client.get(f"/studies/{study_id}/findings")).json()
    assert any(group["kind"] == "recurrent_morphology" for group in findings["groups"])

    # El panel lleva al visor por hora de pared: tiene que ser la misma que la
    # de la banda en el manifest, o el click cae al lado del hallazgo.
    bandas = {item["id"]: item for item in manifest["annotations"]}
    items = [item for group in findings["groups"] for item in group["items"]]
    assert items
    for item in items:
        banda = bandas[item["id"]]
        assert (item["startEpochMs"], item["endEpochMs"]) == (
            banda["startEpochMs"],
            banda["endEpochMs"],
        )


async def test_el_resumen_dice_que_fraccion_del_registro_no_se_pudo_evaluar(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Un informe que no lo declara afirma de más: "no se detectaron arritmias"
    sobre un registro 40 % ilegible no significa lo mismo que sobre uno limpio."""
    study_id, _ = await _study_with_ectopics(client, db, make_patient, make_device, lead_off=True)
    as_user(await make_user(UserRole.ADMIN))

    body = (await client.get(f"/studies/{study_id}/findings")).json()
    quality = body["quality"]
    assert 0.0 < quality["badRatio"] < 0.2
    assert quality["analyzableRatio"] > 0.7
    assert quality["evaluatedMs"] == pytest.approx(900_000, rel=0.01)

    niveles = {interval["level"] for interval in quality["intervals"]}
    assert niveles == {"good", "bad"}
    malo = next(item for item in quality["intervals"] if item["level"] == "bad")
    assert malo["reason"] == "lead_off"
    assert malo["startOffsetMs"] == pytest.approx(300_000, abs=10_000)


async def test_los_intervalos_de_calidad_se_fusionan_entre_bloques(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Un intervalo nunca cruza el borde de un bloque de análisis —el motor
    escribe solo la parte nueva de cada uno—, así que la fusión se hace al leer.
    Si no, dos tramos limpios contiguos de la misma corrida se verían como dos
    tramos distintos sin ningún motivo.

    Dos lotes de un bloque entero cada uno (300 s) y uno con el contexto
    derecho que el segundo bloque espera con la corrida abierta, contiguos en
    `seq` **y en `t0Ms`**: con el reloj reiniciado en cero cada lote abriría
    una corrida nueva, y entre corridas no se funde nada.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    study_id = None
    next_seq = 0
    offset = 0
    for indice, segundos in enumerate((300.0, 300.0, 30.0)):
        signal = synth_ecg(duration_s=segundos, seed=indice + 1)
        samples = [
            Sample(timestamp_ms=(offset + index) * STEP_MS, raw_uV=[value], flags=int(flag))
            for index, (value, flag) in enumerate(
                zip(to_microvolts(signal.signal_mv), signal.flags, strict=True)
            )
        ]
        offset += len(samples)
        # La secuencia tiene que ser contigua: el ACK go-back-N descarta un lote
        # que no continúa al anterior, y el estudio se quedaría con uno solo.
        frames = encode_samples(samples, first_seq=next_seq, boot_id=0, simulated=True)
        next_seq += len(frames)
        body = (await post_frames(client, device, api_key, frames)).json()
        study_id = body["studyId"]
        await process_batch(db, body["batchId"])

    filas = list(
        (
            await db.scalars(
                select(SignalQualityInterval).where(SignalQualityInterval.study_id == study_id)
            )
        ).all()
    )
    assert len(filas) == 2, "una fila por bloque"

    as_user(await make_user(UserRole.ADMIN))
    body = (await client.get(f"/studies/{study_id}/findings")).json()
    assert len(body["quality"]["intervals"]) == 1
    intervalo = body["quality"]["intervals"][0]
    assert intervalo["level"] == "good"
    assert body["quality"]["evaluatedMs"] == pytest.approx(600_000, rel=0.01)
    # Con hora de pared, como los hallazgos: el panel lleva al visor por ahí.
    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    tramo = manifest["timeline"][0]
    assert intervalo["startEpochMs"] == tramo["startEpochMs"]
    # Los 30 s del tercer lote siguen en la cola abierta: el tramo los tiene y
    # la calidad todavía no.
    duracion = tramo["endEpochMs"] - tramo["startEpochMs"]
    assert duracion == pytest.approx(630_000, abs=5)
    assert intervalo["endEpochMs"] - intervalo["startEpochMs"] == pytest.approx(
        duracion * 600 / 630, abs=2
    )


async def test_los_items_por_grupo_se_recortan_por_score_y_no_por_orden(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Si el médico solo va a mirar N de cuatrocientos, tienen que ser los N más
    atípicos, no los N primeros de la noche."""
    study_id, _ = await _study_with_ectopics(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    completo = (await client.get(f"/studies/{study_id}/findings?itemsPerGroup=100")).json()
    recortado = (await client.get(f"/studies/{study_id}/findings?itemsPerGroup=3")).json()

    grupo_completo = next(g for g in completo["groups"] if g["key"].startswith("cluster:"))
    grupo_recortado = next(g for g in recortado["groups"] if g["key"].startswith("cluster:"))

    assert grupo_recortado["occurrences"] == grupo_completo["occurrences"]
    assert len(grupo_recortado["items"]) == 3
    assert recortado["truncated"] is True

    mejores = sorted(
        (item["confidenceScore"] or 0 for item in grupo_completo["items"]), reverse=True
    )[:3]
    assert sorted(
        (item["confidenceScore"] or 0 for item in grupo_recortado["items"]), reverse=True
    ) == pytest.approx(mejores)


async def test_el_acceso_a_los_hallazgos_queda_auditado(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Expone la misma información clínica que el manifest, solo que ordenada de
    otra manera: tiene que dejar la misma huella."""
    study_id, _ = await _study_with_ectopics(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    await client.get(f"/studies/{study_id}/findings")

    events = list(
        (
            await db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == AuditEventType.ECG_ACCESSED)
            )
        ).all()
    )
    assert any(event.event_metadata.get("protocol") == "findings" for event in events)


async def test_un_medico_ajeno_no_ve_los_hallazgos(
    client, s3, db, as_user, make_user, make_doctor, make_patient, make_device
) -> None:
    study_id, _ = await _study_with_ectopics(client, db, make_patient, make_device)
    otro_medico = await make_doctor()
    as_user(await db.get(type(await make_user(UserRole.MEDICO)), otro_medico.user_id))

    response = await client.get(f"/studies/{study_id}/findings")
    assert response.status_code == 404


async def test_sin_sesion_no_dice_nada_de_nadie(client, s3, db, make_patient, make_device) -> None:
    study_id, _ = await _study_with_ectopics(client, db, make_patient, make_device)
    assert (await client.get(f"/studies/{study_id}/findings")).status_code == 401


async def test_un_estudio_sin_analizar_devuelve_una_respuesta_vacia_y_valida(
    client, s3, db, as_user, make_user, make_patient, make_device, make_study
) -> None:
    """Que no haya hallazgos no es un error: es el resultado esperado de un
    Holter normal, y el panel tiene que poder dibujarlo."""
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    as_user(await make_user(UserRole.ADMIN))

    body = (await client.get(f"/studies/{study.id}/findings")).json()
    assert body["groups"] == []
    assert body["ungrouped"] == []
    assert body["quality"]["intervals"] == []
    assert body["modelVersion"] is None
    assert body["truncated"] is False
