"""Notificaciones push al paciente.

Dos superficies distintas:

- `ExpoPushSender`, el cliente HTTP. Lo que importa es que no explote nunca —
  corre en background después de que el trabajo real ya se commiteó — y que dé
  de baja los tokens que Expo reporta muertos, o la lista se pudre.
- El canal corto del chaleco (`POST /ingest/device-status`), que es lo que le
  avisa al paciente que lo tiene mal puesto sin esperar al envío de la hora.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.push import (
    MAX_TOKENS_PER_REQUEST,
    ExpoPushSender,
    NoopPushSender,
    PushMessage,
)
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import DeviceStatus
from app.db.models.push_token import PushToken
from app.db.models.user import User
from app.ml.decompression import FLAG_EVENT_MARKER
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames_with_flag_span, device_headers, post_frames

MESSAGE = PushMessage(title="Hola", body="Contanos cómo te sentís", data={"type": "test"})


# --------------------------------------------------------------------------- #
# Cliente de Expo
# --------------------------------------------------------------------------- #


async def test_sender_trocea_de_a_cien(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expo acepta 100 mensajes por request y cada token es un mensaje."""
    lotes: list[int] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        lotes.append(len(payload))
        return httpx.Response(200, json={"data": [{"status": "ok"} for _ in payload]})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(_handler))
    sender = ExpoPushSender("https://exp.host/--/api/v2/push/send")

    tokens = [f"ExponentPushToken[{i}]" for i in range(250)]
    results = await sender.send(tokens, MESSAGE)

    assert lotes == [MAX_TOKENS_PER_REQUEST, MAX_TOKENS_PER_REQUEST, 50]
    assert len(results) == 250
    assert all(result.ok for result in results)


async def test_sender_marca_los_tokens_muertos(monkeypatch: pytest.MonkeyPatch) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"status": "ok"},
                    {
                        "status": "error",
                        "message": "not registered",
                        "details": {"error": "DeviceNotRegistered"},
                    },
                ]
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(_handler))
    sender = ExpoPushSender("https://exp.host/--/api/v2/push/send")

    results = await sender.send(["vivo", "muerto"], MESSAGE)

    assert [result.is_dead_token for result in results] == [False, True]


async def test_sender_no_explota_si_expo_se_cae(monkeypatch: pytest.MonkeyPatch) -> None:
    """Corre después de que la ingesta ya se commiteó: no puede propagar."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(_handler))
    sender = ExpoPushSender("https://exp.host/--/api/v2/push/send")

    results = await sender.send(["a", "b"], MESSAGE)

    assert [result.ok for result in results] == [False, False]
    assert not any(result.is_dead_token for result in results), (
        "un error de red no puede confundirse con un celular que desinstaló la app"
    )


async def test_respuesta_desalineada_no_da_de_baja_a_nadie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expo devuelve un ticket por mensaje y en orden. Si no, no se adivina."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"status": "ok"}]})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(_handler))
    sender = ExpoPushSender("https://exp.host/--/api/v2/push/send")

    results = await sender.send(["a", "b"], MESSAGE)

    assert not any(result.ok for result in results)
    assert not any(result.is_dead_token for result in results)


async def test_noop_sender_no_falla() -> None:
    results = await NoopPushSender().send(["a"], MESSAGE)

    assert [result.ok for result in results] == [True]


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> type[httpx.AsyncClient]:
    """Sustituto de `httpx.AsyncClient` que responde con `handler`."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class _Client(real):  # type: ignore[valid-type,misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs.pop("transport", None)
            super().__init__(transport=transport, **kwargs)

    return _Client


# --------------------------------------------------------------------------- #
# Registro de tokens
# --------------------------------------------------------------------------- #


async def test_alta_y_baja_de_token(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)
    headers = mobile_headers(account)
    account_id = account.id
    payload = {"token": "ExponentPushToken[abc]", "platform": "android"}

    assert (
        await client.post("/mobile/push-tokens", json=payload, headers=headers)
    ).status_code == 204
    # Reenviarlo (la app lo hace en cada arranque) no puede duplicar la fila.
    assert (
        await client.post("/mobile/push-tokens", json=payload, headers=headers)
    ).status_code == 204
    activos = (
        await db.scalars(
            select(PushToken).where(PushToken.user_id == account_id, PushToken.deleted_at.is_(None))
        )
    ).all()
    assert len(activos) == 1

    assert (
        await client.post("/mobile/push-tokens/remove", json=payload, headers=headers)
    ).status_code == 204
    db.expire_all()
    restantes = (
        await db.scalars(
            select(PushToken).where(PushToken.user_id == account_id, PushToken.deleted_at.is_(None))
        )
    ).all()
    assert restantes == []


async def test_un_token_se_reasigna_en_vez_de_duplicarse(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    """Pasa de verdad: dos personas usan el mismo celular.

    Si la fila se duplicara, el aviso de uno le llegaría también al otro.
    """
    primero = await make_patient_account(await make_patient())
    segundo = await make_patient_account(await make_patient())
    payload = {"token": "ExponentPushToken[compartido]", "platform": "ios"}

    await client.post("/mobile/push-tokens", json=payload, headers=mobile_headers(primero))
    await client.post("/mobile/push-tokens", json=payload, headers=mobile_headers(segundo))
    segundo_id = segundo.id

    filas = (
        await db.scalars(
            select(PushToken).where(
                PushToken.token == payload["token"], PushToken.deleted_at.is_(None)
            )
        )
    ).all()
    assert len(filas) == 1
    assert filas[0].user_id == segundo_id


# --------------------------------------------------------------------------- #
# Canal corto del chaleco
# --------------------------------------------------------------------------- #


async def _device_status(client: AsyncClient, device: Any, api_key: str, **overrides: Any) -> Any:
    payload = {"event": "signal_quality_bad", "durationSeconds": 300, **overrides}
    headers = device_headers(device, api_key)
    headers.pop("Content-Type")
    return await client.post("/ingest/device-status", json=payload, headers=headers)


async def test_aviso_de_mala_colocacion_crea_alerta_sin_evento(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    """La señal de ese momento sigue en la flash del chaleco: no hay `ecg_event`."""
    patient = await make_patient()
    patient_id = patient.id
    device, api_key = await make_device(patient=patient)

    response = await _device_status(client, device, api_key)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notified"] is True
    assert len(sent_pushes) == 1
    notificado, mensaje = sent_pushes[0]
    assert notificado == patient_id
    assert mensaje.data["type"] == "vest_misplaced"
    assert mensaje.data["alertId"] == body["alertId"]
    alert = await db.get(Alert, body["alertId"])
    assert alert is not None
    assert alert.event_id is None
    assert alert.kind == "vest_misplaced"
    assert alert.severity.value == "HIGH"
    assert "5 min" in alert.message


async def test_avisos_repetidos_no_bombardean_al_paciente(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    """Un chaleco que rebota mientras alguien se lo acomoda manda muchos avisos."""
    patient = await make_patient()
    patient_id = patient.id
    device, api_key = await make_device(patient=patient)

    primero = await _device_status(client, device, api_key)
    segundo = await _device_status(client, device, api_key, durationSeconds=420)

    assert primero.json()["notified"] is True
    assert segundo.json()["notified"] is False
    assert segundo.json()["alertId"] == primero.json()["alertId"]
    assert len(sent_pushes) == 1, "el segundo aviso no puede volver a sonar"
    alertas = (await db.scalars(select(Alert).where(Alert.patient_id == patient_id))).all()
    assert len(alertas) == 1


async def test_senal_recuperada_no_genera_alerta(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
) -> None:
    patient = await make_patient()
    patient_id = patient.id
    device, api_key = await make_device(patient=patient)

    response = await _device_status(
        client, device, api_key, event="signal_recovered", durationSeconds=0
    )

    assert response.status_code == 200
    assert response.json()["notified"] is False
    alertas = (await db.scalars(select(Alert).where(Alert.patient_id == patient_id))).all()
    assert alertas == []


async def test_la_colocacion_queda_guardada_en_el_equipo(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
) -> None:
    """El estado actual es un dato del equipo, no una inferencia sobre las alertas.

    Sin esto la app tenía que adivinar por tiempo si el chaleco seguía mal
    puesto, y acomodárselo no apagaba el cartel hasta que pasara la ventana.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    assert device.placement_ok is None, "un equipo recién entregado no reportó nada"

    await _device_status(client, device, api_key)
    await db.refresh(device)
    assert device.placement_ok is False
    assert device.placement_reported_at is not None

    await _device_status(client, device, api_key, event="signal_recovered", durationSeconds=0)
    await db.refresh(device)
    assert device.placement_ok is True


async def test_un_episodio_nuevo_vuelve_a_avisar(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    """El debounce protege contra el rebote, no contra un episodio distinto.

    Si el chaleco se acomodó y se volvió a soltar cinco minutos después, eso es
    algo nuevo que el paciente tiene que saber. Absorberlo por la ventana de 30
    minutos dejaba el aviso mudo justo cuando volvía a importar.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    primero = await _device_status(client, device, api_key)
    await _device_status(client, device, api_key, event="signal_recovered", durationSeconds=0)
    segundo = await _device_status(client, device, api_key)

    assert primero.json()["notified"] is True
    assert segundo.json()["notified"] is True
    assert segundo.json()["alertId"] != primero.json()["alertId"]
    assert len(sent_pushes) == 2


async def test_equipo_sin_paciente_no_notifica(
    client: AsyncClient,
    make_device: Callable[..., Any],
) -> None:
    device, api_key = await make_device()

    response = await _device_status(client, device, api_key)

    assert response.status_code == 200
    assert response.json()["notified"] is False


async def test_device_status_exige_credenciales(
    client: AsyncClient, make_device: Callable[..., Any]
) -> None:
    device, _ = await make_device()

    response = await client.post(
        "/ingest/device-status",
        json={"event": "signal_quality_bad", "durationSeconds": 60},
        headers={"X-Device-Serial": device.serial_number, "X-Device-Uptime-Ms": "1000"},
    )

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Push por anomalía, y la bitácora que lo responde
# --------------------------------------------------------------------------- #


async def test_una_anomalia_notifica_una_sola_vez_y_abre_la_bitacora(
    client: AsyncClient,
    s3: None,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    make_device: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    """El circuito completo del requerimiento "Bitácora (Post-Push)"."""
    patient = await make_patient()
    account = await make_patient_account(patient)
    device, api_key = await make_device(patient=patient)

    # Marcas de síntoma en el mismo lote: una sola notificación.
    frames = build_frames_with_flag_span(1200, span=(400, 404), flags=FLAG_EVENT_MARKER)
    body = (await post_frames(client, device, api_key, frames)).json()
    await process_batch(db, body["batchId"])

    assert len(sent_pushes) == 1, "un lote de 1 h no puede disparar una notificación por evento"
    _, mensaje = sent_pushes[0]
    assert mensaje.data["type"] == "report_request"
    # El tipo de hallazgo viaja con el aviso: nombra el título del push y
    # encabeza el formulario que abre, para que el paciente pueda reconstruir
    # qué estaba haciendo en ese momento.
    assert mensaje.data["kind"] == "symptom_marker"
    assert "síntoma" in mensaje.title
    alert_id = mensaje.data["alertId"]

    # El paciente responde el formulario que abrió la notificación.
    headers = mobile_headers(account)
    primera = await client.post(
        "/mobile/reports",
        json={"alertId": alert_id, "symptoms": ["palpitaciones"], "activity": "caminando"},
        headers=headers,
    )
    assert primera.status_code == 201, primera.text
    assert primera.json()["source"] == "push_response"
    assert primera.json()["alertId"] == alert_id

    # Doble tap sobre la notificación: actualiza, no duplica el dato clínico.
    segunda = await client.post(
        "/mobile/reports",
        json={"alertId": alert_id, "symptoms": ["mareo"], "activity": "reposo"},
        headers=headers,
    )
    assert segunda.status_code == 201, segunda.text
    assert segunda.json()["id"] == primera.json()["id"]
    assert segunda.json()["symptoms"] == ["mareo"]
    assert (await client.get("/mobile/reports", headers=headers)).json()["total"] == 1

    # Y el aviso deja de pedir respuesta en Inicio.
    avisos = (await client.get("/mobile/alerts", headers=headers)).json()
    marcado = next(item for item in avisos["items"] if item["id"] == alert_id)
    assert marcado["requiresResponse"] is True
    assert marcado["needsReport"] is False
    assert marcado["reportId"] == primera.json()["id"]
    assert marcado["answeredAt"] is not None
    assert avisos["pendingTotal"] == 0


async def test_historial_de_avisos_pagina_y_filtra_por_estado(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)
    now = datetime.now(UTC)
    vest = Alert(
        patient_id=patient.id,
        kind="vest_misplaced",
        severity=AlertSeverity.HIGH,
        message="Revisá el chaleco.",
        created_at=now,
    )
    pendiente_nueva = Alert(
        patient_id=patient.id,
        kind="other",
        severity=AlertSeverity.MEDIUM,
        message="Contanos cómo te sentiste.",
        created_at=now - timedelta(minutes=1),
    )
    respondida = Alert(
        patient_id=patient.id,
        kind="other",
        severity=AlertSeverity.LOW,
        message="Aviso ya respondido.",
        created_at=now - timedelta(minutes=2),
    )
    pendiente_vieja = Alert(
        patient_id=patient.id,
        kind="other",
        severity=AlertSeverity.LOW,
        message="Otro aviso pendiente.",
        created_at=now - timedelta(minutes=3),
    )
    db.add_all([vest, pendiente_nueva, respondida, pendiente_vieja])
    await db.flush()

    headers = mobile_headers(account)
    respuesta = await client.post(
        "/mobile/reports",
        json={"alertId": str(respondida.id), "symptoms": ["mareo"], "activity": "reposo"},
        headers=headers,
    )
    assert respuesta.status_code == 201, respuesta.text

    pagina = await client.get("/mobile/alerts?limit=2&offset=1", headers=headers)
    assert pagina.status_code == 200, pagina.text
    body = pagina.json()
    assert body["total"] == 4
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert [item["id"] for item in body["items"]] == [
        str(pendiente_nueva.id),
        str(respondida.id),
    ]
    assert body["pendingTotal"] == 2
    assert body["items"][1]["reportId"] == respuesta.json()["id"]
    assert body["items"][1]["answeredAt"] is not None

    pendientes = await client.get("/mobile/alerts?limit=10&status=pending", headers=headers)
    assert pendientes.status_code == 200, pendientes.text
    pending_body = pendientes.json()
    assert pending_body["total"] == 2
    assert pending_body["pendingTotal"] == 2
    assert [item["id"] for item in pending_body["items"]] == [
        str(pendiente_nueva.id),
        str(pendiente_vieja.id),
    ]

    # "Respondidas" no es el complemento de "pendientes": el aviso del chaleco
    # no pide respuesta, así que no entra en ninguno de los dos filtros.
    respondidas = await client.get("/mobile/alerts?limit=10&status=answered", headers=headers)
    assert respondidas.status_code == 200, respondidas.text
    answered_body = respondidas.json()
    assert answered_body["total"] == 1
    assert answered_body["pendingTotal"] == 2
    assert [item["id"] for item in answered_body["items"]] == [str(respondida.id)]

    all_items = (await client.get("/mobile/alerts?limit=10", headers=headers)).json()["items"]
    vest_out = next(item for item in all_items if item["id"] == str(vest.id))
    assert vest_out["requiresResponse"] is False
    assert vest_out["needsReport"] is False
    assert vest_out["reportId"] is None
    assert vest_out["answeredAt"] is None


async def test_bandeja_actionable_muestra_solo_el_episodio_vigente_del_chaleco(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    make_device: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    account = await make_patient_account(patient)
    device, _ = await make_device(patient=patient, placement_ok=False)
    now = datetime.now(UTC)
    vest_vieja = Alert(
        patient_id=patient.id,
        kind="vest_misplaced",
        severity=AlertSeverity.HIGH,
        message="Primer episodio de mala colocación.",
        created_at=now - timedelta(minutes=10),
    )
    vest_vigente = Alert(
        patient_id=patient.id,
        kind="vest_misplaced",
        severity=AlertSeverity.HIGH,
        message="El chaleco sigue mal colocado.",
        created_at=now - timedelta(minutes=2),
    )
    pendiente = Alert(
        patient_id=patient.id,
        kind="afib",
        severity=AlertSeverity.CRITICAL,
        message="Contanos cómo te sentiste.",
        created_at=now,
    )
    respondida = Alert(
        patient_id=patient.id,
        kind="other",
        severity=AlertSeverity.MEDIUM,
        message="Aviso ya respondido.",
        created_at=now - timedelta(minutes=1),
    )
    db.add_all([vest_vieja, vest_vigente, pendiente, respondida])
    await db.flush()

    headers = mobile_headers(account)
    respuesta = await client.post(
        "/mobile/reports",
        json={"alertId": str(respondida.id), "symptoms": ["mareo"], "activity": "reposo"},
        headers=headers,
    )
    assert respuesta.status_code == 201, respuesta.text

    first_page = await client.get("/mobile/alerts?limit=1&status=actionable", headers=headers)
    second_page = await client.get(
        "/mobile/alerts?limit=1&offset=1&status=actionable", headers=headers
    )
    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    assert first_page.json()["total"] == 2
    assert first_page.json()["pendingTotal"] == 1
    assert [item["id"] for item in first_page.json()["items"]] == [str(vest_vigente.id)]
    assert [item["id"] for item in second_page.json()["items"]] == [str(pendiente.id)]

    # La recuperación cambia estado, no borra historia: el aviso desaparece de
    # la bandeja operativa pero sigue disponible en `all`.
    device.placement_ok = True
    await db.flush()
    recovered = await client.get("/mobile/alerts?limit=10&status=actionable", headers=headers)
    assert [item["id"] for item in recovered.json()["items"]] == [str(pendiente.id)]
    assert recovered.json()["total"] == 1
    assert recovered.json()["pendingTotal"] == 1

    # Sin una medición actual o sin equipo asignado tampoco se afirma que el
    # chaleco está mal colocado.
    device.placement_ok = None
    await db.flush()
    unknown = await client.get("/mobile/alerts?status=actionable", headers=headers)
    assert [item["id"] for item in unknown.json()["items"]] == [str(pendiente.id)]

    device.placement_ok = False
    device.patient_id = None
    device.status = DeviceStatus.AVAILABLE
    await db.flush()
    unassigned = await client.get("/mobile/alerts?status=actionable", headers=headers)
    assert [item["id"] for item in unassigned.json()["items"]] == [str(pendiente.id)]


def test_el_titulo_del_aviso_nombra_el_hallazgo() -> None:
    """Un push que no dice de qué es, no se abre.

    "Registrá cómo te sentís" describía la tarea y no el motivo, así que en la
    bandeja era indistinguible de cualquier otro recordatorio. El título ahora
    nombra el hallazgo con las mismas palabras que usa la app.
    """
    from app.modules.patient_app.notifications_service import anomaly_message

    afib = anomaly_message(uuid.uuid4(), "2026-09-04T10:00:00+00:00", "afib")

    assert afib.title == "Tu chaleco registró un ritmo irregular"
    assert afib.data["kind"] == "afib"
    # El cuerpo cierra con la acción: Expo no dibuja botones, así que el CTA
    # tiene que estar en el texto o no existe.
    assert afib.body.endswith("Tocá para responder, es un minuto.")


def test_un_tipo_desconocido_no_deja_el_aviso_mudo() -> None:
    """Un `kind` nuevo del pipeline no puede romper la notificación."""
    from app.modules.patient_app.notifications_service import anomaly_message

    sin_kind = anomaly_message(uuid.uuid4(), "2026-09-04T10:00:00+00:00")
    raro = anomaly_message(uuid.uuid4(), "2026-09-04T10:00:00+00:00", "algo_nuevo")

    assert sin_kind.title == "Hay un momento de tu registro para revisar"
    assert raro.title == sin_kind.title
    # Sin `kind` la clave no viaja: el formulario cae en su etiqueta genérica.
    assert "kind" not in sin_kind.data
