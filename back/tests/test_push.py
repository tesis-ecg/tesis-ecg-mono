"""Notificaciones push al paciente.

Dos superficies distintas:

- `ExpoPushSender`, el cliente HTTP. Lo que importa es que no explote nunca —
  corre en background después de que el trabajo real ya se commiteó — y que dé
  de baja los tokens que Expo reporta muertos, o la lista se pudre.
- El canal corto del chaleco (`POST /ingest/device-status`), que es lo que le
  avisa al paciente que lo tiene mal puesto sin esperar al envío de la hora.
"""

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
