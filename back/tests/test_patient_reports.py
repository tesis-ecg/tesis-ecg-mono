"""Bitácora del paciente.

El caso que justifica que esta tabla exista y no sea un `ecg_event`: el chaleco
sube tramas una vez por hora, así que el paciente puede marcar un síntoma
mucho antes de que exista la señal de ese instante. El registro tiene que
guardarse igual, no puede pintarse todavía sobre el ECG, y tiene que aparecer
solo —sin job ni backfill— cuando llegue el lote que lo cubre.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.study import Study
from app.db.models.user import User, UserRole
from app.ml.decompression import STEP_MS
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames, post_frames

SYMPTOM_REPORT: dict[str, Any] = {
    "symptoms": ["palpitaciones"],
    "activity": "subiendo_escaleras",
    "notes": "Se me pasó al sentarme.",
}


async def _ingest(
    client: AsyncClient,
    db: AsyncSession,
    device: Any,
    api_key: str,
    samples: int,
    *,
    first_seq: int = 0,
    t0_ms: int = 0,
) -> tuple[dict[str, Any], int]:
    frames = build_frames(samples, first_seq=first_seq, t0_ms=t0_ms)
    body = (await post_frames(client, device, api_key, frames)).json()
    await process_batch(db, body["batchId"])
    return body, len(frames)


# --------------------------------------------------------------------------- #
# El caso central
# --------------------------------------------------------------------------- #


async def test_un_registro_sin_senal_todavia_no_se_pinta_y_despues_si(
    client: AsyncClient,
    s3: None,
    db: AsyncSession,
    as_user: Callable[[User], AsyncClient],
    make_user: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    make_device: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)
    device, api_key = await make_device(patient=patient)

    # 2000 muestras a 500 Hz = 4 s de señal grabada.
    body, frame_count = await _ingest(client, db, device, api_key, 2000)
    study_id = body["studyId"]
    study = await db.get(Study, study_id)
    assert study is not None
    started_at = study.started_at

    headers = mobile_headers(account)
    dentro = await client.post(
        "/mobile/reports",
        json={**SYMPTOM_REPORT, "occurredAt": (started_at + timedelta(seconds=1)).isoformat()},
        headers=headers,
    )
    fuera = await client.post(
        "/mobile/reports",
        json={**SYMPTOM_REPORT, "occurredAt": (started_at + timedelta(seconds=6)).isoformat()},
        headers=headers,
    )
    assert dentro.status_code == 201, dentro.text
    assert fuera.status_code == 201, fuera.text
    dentro_id, fuera_id = dentro.json()["id"], fuera.json()["id"]
    # Los dos se guardan y los dos quedan atados al estudio: la ventana
    # administrativa los cubre aunque la señal todavía no.
    assert dentro.json()["studyId"] == study_id
    assert fuera.json()["studyId"] == study_id

    as_user(await make_user(UserRole.ADMIN))
    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    marcadores = {
        item["id"]: item for item in manifest["annotations"] if item["kind"] == "patient_report"
    }
    assert dentro_id in marcadores
    assert fuera_id not in marcadores, (
        "un registro sin señal debajo no se puede pintar; recortarlo contra el "
        "final de la traza sería marcar un instante donde no pasó nada"
    )
    assert marcadores[dentro_id]["startOffsetMs"] == 1000
    assert marcadores[dentro_id]["endOffsetMs"] == 1000
    assert marcadores[dentro_id]["category"] == "patient_marker"

    # La solapa del médico sí los muestra a los dos, y distingue cuál falta.
    reportes = (await client.get(f"/studies/{study_id}/patient-reports")).json()
    assert reportes["total"] == 2
    assert reportes["pendingSignalTotal"] == 1
    por_id = {item["id"]: item for item in reportes["items"]}
    assert por_id[dentro_id]["visibleInChart"] is True
    assert por_id[fuera_id]["visibleInChart"] is False
    assert por_id[fuera_id]["offsetMs"] is None

    # Llega el lote siguiente: 8 s grabados, el registro de los 6 s ya tiene
    # señal debajo y aparece solo.
    await _ingest(client, db, device, api_key, 2000, first_seq=frame_count, t0_ms=2000 * STEP_MS)

    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    marcadores = {
        item["id"]: item for item in manifest["annotations"] if item["kind"] == "patient_report"
    }
    assert fuera_id in marcadores, "al llegar el lote el registro migra solo a la banda"
    assert marcadores[fuera_id]["startOffsetMs"] == 6000
    reportes = (await client.get(f"/studies/{study_id}/patient-reports")).json()
    assert reportes["pendingSignalTotal"] == 0


# --------------------------------------------------------------------------- #
# Alta de registros
# --------------------------------------------------------------------------- #


async def test_registro_manual_sin_estudio_abierto(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    """Vive en el historial del paciente y no se pinta en ningún gráfico."""
    patient = await make_patient()
    account = await make_patient_account(patient)

    response = await client.post(
        "/mobile/reports", json=SYMPTOM_REPORT, headers=mobile_headers(account)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["studyId"] is None
    assert body["source"] == "manual"
    assert body["alertId"] is None


async def test_otro_sin_texto_libre_es_422(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)
    headers = mobile_headers(account)

    sintoma = await client.post(
        "/mobile/reports",
        json={"symptoms": ["otro"], "activity": "reposo"},
        headers=headers,
    )
    actividad = await client.post(
        "/mobile/reports",
        json={"symptoms": ["mareo"], "activity": "otro"},
        headers=headers,
    )

    assert sintoma.status_code == 422
    assert sintoma.json()["code"] == "SYMPTOM_DETAIL_REQUIRED"
    assert actividad.status_code == 422
    assert actividad.json()["code"] == "ACTIVITY_DETAIL_REQUIRED"


async def test_sin_sintomas_no_se_combina(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    """Marcar "no sentí nada" junto a "dolor de pecho" no lo puede leer nadie."""
    patient = await make_patient()
    account = await make_patient_account(patient)

    response = await client.post(
        "/mobile/reports",
        json={"symptoms": ["sin_sintomas", "dolor_pecho"], "activity": "reposo"},
        headers=mobile_headers(account),
    )

    assert response.status_code == 422


async def test_sintoma_desconocido_es_422(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)

    response = await client.post(
        "/mobile/reports",
        json={"symptoms": ["me_duele_todo"], "activity": "reposo"},
        headers=mobile_headers(account),
    )

    assert response.status_code == 422


async def test_reloj_desfasado_se_acota(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    """Un celular con la fecha corrida no puede anclar un síntoma a 2019."""
    patient = await make_patient()
    account = await make_patient_account(patient)

    response = await client.post(
        "/mobile/reports",
        json={**SYMPTOM_REPORT, "occurredAt": "2019-01-01T00:00:00+00:00"},
        headers=mobile_headers(account),
    )

    assert response.status_code == 201, response.text
    occurred_at = datetime.fromisoformat(response.json()["occurredAt"])
    assert occurred_at > datetime.now(UTC) - timedelta(hours=25)


async def test_historial_lista_lo_cargado(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)
    headers = mobile_headers(account)
    for _ in range(3):
        assert (
            await client.post("/mobile/reports", json=SYMPTOM_REPORT, headers=headers)
        ).status_code == 201

    response = await client.get("/mobile/reports", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3


async def test_un_paciente_no_ve_la_bitacora_de_otro(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    ajeno = await make_patient()
    cuenta_ajena = await make_patient_account(ajeno)
    creado = await client.post(
        "/mobile/reports", json=SYMPTOM_REPORT, headers=mobile_headers(cuenta_ajena)
    )
    assert creado.status_code == 201
    report_id = creado.json()["id"]

    propio = await client.get(f"/mobile/reports/{report_id}", headers=mobile_headers(cuenta_ajena))
    assert propio.status_code == 200
    assert propio.json()["id"] == report_id

    otro = await make_patient()
    cuenta_propia = await make_patient_account(otro)

    response = await client.get("/mobile/reports", headers=mobile_headers(cuenta_propia))

    assert response.status_code == 200
    assert response.json()["total"] == 0
    detalle_ajeno = await client.get(
        f"/mobile/reports/{report_id}", headers=mobile_headers(cuenta_propia)
    )
    assert detalle_ajeno.status_code == 404
    assert detalle_ajeno.json()["code"] == "REPORT_NOT_FOUND"


async def test_catalogos(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)

    response = await client.get("/mobile/catalogs", headers=mobile_headers(account))

    assert response.status_code == 200, response.text
    body = response.json()
    assert {item["value"] for item in body["symptoms"]} >= {"palpitaciones", "dolor_pecho"}
    assert {item["value"] for item in body["activities"]} >= {"durmiendo", "ejercicio"}


# --------------------------------------------------------------------------- #
# Respuesta a un aviso
# --------------------------------------------------------------------------- #


async def test_la_respuesta_a_un_aviso_queda_pegada_al_hallazgo_que_contesta(
    client: AsyncClient,
    s3: None,
    db: AsyncSession,
    as_user: Callable[[User], AsyncClient],
    make_user: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    make_device: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    """El flujo completo: hallazgo → aviso → respuesta → marca sobre la banda.

    Lo que fija el test es la pertenencia. El paciente contesta cuando ve la
    notificación, y por hora de pared esa marca cae en un instante que el
    chaleco todavía no subió: suelta, no se pintaría nunca. Anclada al hallazgo
    que responde cae en el medio de su banda y viaja con `linkedAnnotationId`,
    que es lo que le dice al médico a cuál de los avisos pertenece.
    """
    patient = await make_patient()
    account = await make_patient_account(patient)
    device, api_key = await make_device(patient=patient)
    # 4 s grabados: el hallazgo se ancla adentro, la respuesta llega "ahora".
    body, _ = await _ingest(client, db, device, api_key, 2000)
    study_id = body["studyId"]

    admin = await make_user(UserRole.ADMIN)
    as_user(admin)
    anomalia = await client.post(
        f"/studies/{study_id}/simulate-anomaly",
        json={
            "eventType": "tachycardia",
            "severity": "high",
            "secondsBeforeEnd": 3,
            "durationSeconds": 2,
        },
    )
    assert anomalia.status_code == 200, anomalia.text
    alert_id, event_id = anomalia.json()["alertId"], anomalia.json()["eventId"]

    # Sin `occurredAt`: se guarda con la hora del aviso, que es "ahora" y cae
    # más allá de lo grabado. Es el caso que dejaba la respuesta invisible.
    respuesta = await client.post(
        "/mobile/reports",
        json={"alertId": alert_id, "symptoms": ["palpitaciones"], "activity": "reposo"},
        headers=mobile_headers(account),
    )
    assert respuesta.status_code == 201, respuesta.text
    report_id = respuesta.json()["id"]

    as_user(admin)
    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    por_id = {item["id"]: item for item in manifest["annotations"]}
    hallazgo, marca = por_id[event_id], por_id[report_id]

    assert marca["linkedAnnotationId"] == event_id
    assert marca["description"] == "Palpitaciones"
    assert marca["startOffsetMs"] == (hallazgo["startOffsetMs"] + hallazgo["endOffsetMs"]) // 2

    # La solapa ubica el registro en el mismo lugar: si dijera "sin señal", el
    # botón "Ver en el ECG" faltaría para una marca que el visor sí dibuja.
    reportes = (await client.get(f"/studies/{study_id}/patient-reports")).json()
    assert reportes["pendingSignalTotal"] == 0
    registro = reportes["items"][0]
    assert registro["visibleInChart"] is True
    assert registro["offsetMs"] == marca["startOffsetMs"]
    assert registro["alertKind"] == "tachycardia"


async def test_un_registro_espontaneo_conserva_su_hora_de_pared(
    client: AsyncClient,
    s3: None,
    db: AsyncSession,
    as_user: Callable[[User], AsyncClient],
    make_user: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    make_device: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    """Sin aviso detrás no hay a qué anclarse, y el registro se ubica solo."""
    patient = await make_patient()
    account = await make_patient_account(patient)
    device, api_key = await make_device(patient=patient)
    body, _ = await _ingest(client, db, device, api_key, 2000)
    study_id = body["studyId"]
    study = await db.get(Study, study_id)
    assert study is not None

    espontaneo = await client.post(
        "/mobile/reports",
        json={
            **SYMPTOM_REPORT,
            "occurredAt": (study.started_at + timedelta(seconds=2)).isoformat(),
        },
        headers=mobile_headers(account),
    )
    assert espontaneo.status_code == 201, espontaneo.text

    as_user(await make_user(UserRole.ADMIN))
    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    marca = {item["id"]: item for item in manifest["annotations"]}[espontaneo.json()["id"]]

    assert marca["linkedAnnotationId"] is None
    assert marca["startOffsetMs"] == 2000
