"""Disparador manual de anomalías, el reemplazo temporal del pipeline de ML.

`app/ml/*` son stubs: la única alerta `HIGH` que el sistema sabe producir hoy
sale del botón de síntoma del chaleco. Sin este endpoint no hay forma de
ejercitar el aviso de "detectamos algo", el formulario de la bitácora que lo
responde ni la marca resultante sobre el ECG.

Lo que fijan estos tests es lo que distingue esto de crear una alerta suelta: el
hallazgo se ancla **dentro de la señal ya ingerida**, y de ahí sale el
`occurredAt` que hace que la respuesta del paciente sea ubicable en el gráfico.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import Environment, settings
from app.core.push import PushMessage
from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent
from app.db.models.user import User, UserRole

SAMPLE_RATE = 500
URL = "/studies/{study_id}/simulate-anomaly"


async def _batch(db: Any, device: Any, study: Any, num_samples: int) -> ECGBatch:
    batch = ECGBatch(
        device_id=device.id,
        study_id=study.id,
        received_at=datetime.now(UTC),
        batch_timestamp=0,
        duration_seconds=num_samples // SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        num_samples=num_samples,
        compression_type="rice",
        s3_key="",
        processing_status=ProcessingStatus.DONE,
        first_seq=0,
        last_seq=0,
    )
    db.add(batch)
    await db.flush()
    return batch


async def _admin(db: Any, make_user: Any) -> User:
    return await make_user(role=UserRole.ADMIN)


async def _recorded_study(db: Any, make_patient: Any, make_device: Any, make_study: Any) -> Any:
    """Un estudio con una hora de señal ya ingerida detrás."""
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(
        patient,
        device,
        started_at=datetime.now(UTC) - timedelta(hours=1),
        samples_count=SAMPLE_RATE * 3600,
    )
    await _batch(db, device, study, SAMPLE_RATE * 3600)
    return patient, device, study


async def test_la_anomalia_queda_anclada_dentro_de_lo_grabado(
    db: Any,
    as_user: Any,
    make_user: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    patient, _, study = await _recorded_study(db, make_patient, make_device, make_study)
    client: AsyncClient = as_user(await _admin(db, make_user))

    response = await client.post(
        URL.format(study_id=study.id),
        json={"eventType": "afib", "severity": "high", "secondsBeforeEnd": 30},
    )

    assert response.status_code == 200, response.text
    body = response.json()

    event = await db.get(ECGEvent, body["eventId"])
    assert event is not None
    assert event.event_metadata["startSampleIndex"] == SAMPLE_RATE * 3600 - SAMPLE_RATE * 30
    assert event.event_metadata["studyId"] == str(study.id)
    assert event.event_metadata["simulated"] is True

    alert = await db.get(Alert, body["alertId"])
    assert alert is not None
    assert alert.event_id == event.id
    # Sin `kind`: se deriva del evento, igual que una alerta del pipeline real.
    assert alert.kind is None

    # El offset cae 30 s antes del final de la grabación, no en "ahora": es lo
    # que hace ubicable la respuesta del paciente sobre la traza.
    assert body["offsetMs"] == (3600 - 30) * 1000
    assert len(sent_pushes) == 1
    notificado, mensaje = sent_pushes[0]
    assert notificado == patient.id
    assert mensaje.data["type"] == "report_request"
    assert mensaje.data["alertId"] == body["alertId"]
    # Se comparan instantes y no strings: el JSON de la respuesta serializa el
    # UTC como `Z` y `isoformat()` como `+00:00`.
    assert datetime.fromisoformat(mensaje.data["occurredAt"]) == datetime.fromisoformat(
        body["occurredAt"]
    )


async def test_el_ancla_nunca_se_va_antes_del_inicio(
    db: Any,
    as_user: Any,
    make_user: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    """Pedir un hallazgo de hace más tiempo del grabado lo ancla en la muestra 0."""
    _, _, study = await _recorded_study(db, make_patient, make_device, make_study)
    client: AsyncClient = as_user(await _admin(db, make_user))

    response = await client.post(
        URL.format(study_id=study.id),
        json={"eventType": "pause", "secondsBeforeEnd": 86_400},
    )

    assert response.status_code == 200, response.text
    assert response.json()["offsetMs"] == 0


async def test_un_estudio_sin_senal_no_puede_tener_hallazgos(
    db: Any,
    as_user: Any,
    make_user: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
) -> None:
    """409 explícito y no un 500 por el FK de `ecg_event.batch_id`."""
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    client: AsyncClient = as_user(await _admin(db, make_user))

    response = await client.post(URL.format(study_id=study.id), json={})

    assert response.status_code == 409
    assert response.json()["code"] == "STUDY_HAS_NO_SIGNAL"


async def test_un_medico_no_puede_fabricar_hallazgos(
    db: Any,
    as_user: Any,
    make_doctor: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
) -> None:
    """Escribe historia clínica falsa: es una herramienta de banco, no del médico."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    doctor_user = await db.get(User, doctor.user_id)
    client: AsyncClient = as_user(doctor_user)

    response = await client.post(URL.format(study_id=study.id), json={})

    assert response.status_code == 403


async def test_fuera_de_desarrollo_no_existe(
    db: Any,
    as_user: Any,
    make_user: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, study = await _recorded_study(db, make_patient, make_device, make_study)
    client: AsyncClient = as_user(await _admin(db, make_user))
    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)

    # Con el entorno endurecido también se prende el chequeo de Origin, que
    # rechazaría antes de llegar al servicio: mandarlo es lo que deja que el
    # test afirme sobre la regla que le importa y no sobre el middleware.
    response = await client.post(
        URL.format(study_id=study.id),
        json={},
        headers={"Origin": str(settings.frontend_url).rstrip("/")},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "SIMULATION_DISABLED"


async def test_el_hallazgo_aparece_en_el_manifest_del_estudio(
    db: Any,
    as_user: Any,
    make_user: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
) -> None:
    """Lo que se fabrica tiene que verse en el gráfico, no solo en la tabla `alert`."""
    _, _, study = await _recorded_study(db, make_patient, make_device, make_study)
    admin = await _admin(db, make_user)
    client: AsyncClient = as_user(admin)

    await client.post(
        URL.format(study_id=study.id),
        json={"eventType": "tachycardia", "severity": "critical", "durationSeconds": 10},
    )

    events = (
        await db.scalars(
            select(ECGEvent).where(ECGEvent.event_metadata["studyId"].astext == str(study.id))
        )
    ).all()
    assert len(events) == 1
    assert events[0].severity.value == "CRITICAL"
    assert events[0].duration_seconds == 10
