"""Notificaciones push al paciente.

El servicio concreto es el de Expo —gratis y sin tope de volumen, que es lo que
necesita una tesis—, pero el resto del backend nunca lo nombra: habla contra
`PushSender`. Si mañana hay que ir directo a FCM/APNs, se cambia la
implementación y no los llamadores.

El envío **siempre** va en `BackgroundTasks`. El backend corre en Vercel y una
ingesta de tramas no se puede caer porque `exp.host` tardó en responder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

#: Expo acepta hasta 100 mensajes por request. Con `to` como lista, cada token
#: cuenta como un mensaje.
MAX_TOKENS_PER_REQUEST = 100

#: Lo que devuelve Expo cuando el celular desinstaló la app o revocó el permiso.
#: Es la señal para dar de baja el token.
DEVICE_NOT_REGISTERED = "DeviceNotRegistered"

#: Canal de Android. Tiene que coincidir con el que crea la app en
#: `setNotificationChannelAsync`, o el aviso llega sin sonido ni prioridad.
ANDROID_CHANNEL = "alerts"


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PushResult:
    token: str
    ok: bool
    error: str | None = None

    @property
    def is_dead_token(self) -> bool:
        return self.error == DEVICE_NOT_REGISTERED


class PushSender(Protocol):
    async def send(self, tokens: list[str], message: PushMessage) -> list[PushResult]: ...


class NoopPushSender:
    """Sender de desarrollo, tests y CI.

    Registra qué se habría mandado en vez de salir a internet. Que loguee (y no
    que sea un `pass`) es a propósito: sin eso, un push que nunca se dispara por
    un bug de lógica es indistinguible de uno que se dispara bien.
    """

    async def send(self, tokens: list[str], message: PushMessage) -> list[PushResult]:
        await logger.ainfo(
            "push_skipped",
            reason="expo_push_disabled",
            tokens=len(tokens),
            title=message.title,
            data=message.data,
        )
        return [PushResult(token=token, ok=True) for token in tokens]


class ExpoPushSender:
    """Cliente del Expo Push Service."""

    def __init__(self, url: str, access_token: str | None = None, timeout: float = 15.0) -> None:
        self._url = url
        self._access_token = access_token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _payload(self, chunk: list[str], message: PushMessage) -> list[dict[str, Any]]:
        return [
            {
                "to": token,
                "title": message.title,
                "body": message.body,
                "data": message.data,
                "sound": "default",
                # `high` y no `default`: el aviso de que el chaleco está mal
                # colocado pierde el sentido si el celular lo agrupa para más
                # tarde. Son pocos por día y siempre accionables.
                "priority": "high",
                "channelId": ANDROID_CHANNEL,
            }
            for token in chunk
        ]

    async def send(self, tokens: list[str], message: PushMessage) -> list[PushResult]:
        if not tokens:
            return []
        results: list[PushResult] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start in range(0, len(tokens), MAX_TOKENS_PER_REQUEST):
                chunk = tokens[start : start + MAX_TOKENS_PER_REQUEST]
                results.extend(await self._send_chunk(client, chunk, message))
        return results

    async def _send_chunk(
        self, client: httpx.AsyncClient, chunk: list[str], message: PushMessage
    ) -> list[PushResult]:
        try:
            response = await client.post(
                self._url, json=self._payload(chunk, message), headers=self._headers()
            )
        except httpx.HTTPError as exc:
            # Una caída de Expo no puede propagarse: el llamador está en una
            # background task cuyo trabajo real (ingerir señal) ya terminó bien.
            await logger.awarning("push_transport_error", error=str(exc), tokens=len(chunk))
            return [PushResult(token=token, ok=False, error="TRANSPORT_ERROR") for token in chunk]

        if response.status_code != 200:
            await logger.awarning("push_http_error", status=response.status_code, tokens=len(chunk))
            return [PushResult(token=token, ok=False, error="HTTP_ERROR") for token in chunk]

        return self._parse(chunk, response)

    @staticmethod
    def _parse(chunk: list[str], response: httpx.Response) -> list[PushResult]:
        try:
            body = response.json()
        except ValueError:
            return [PushResult(token=token, ok=False, error="BAD_RESPONSE") for token in chunk]
        tickets = body.get("data") if isinstance(body, dict) else None
        if not isinstance(tickets, list) or len(tickets) != len(chunk):
            # Expo devuelve un ticket por mensaje y en el mismo orden. Si no
            # coinciden no se puede saber qué token falló: mejor no adivinar y
            # no dar de baja a nadie por error.
            return [PushResult(token=token, ok=False, error="BAD_RESPONSE") for token in chunk]

        results: list[PushResult] = []
        for token, ticket in zip(chunk, tickets, strict=True):
            if isinstance(ticket, dict) and ticket.get("status") == "ok":
                results.append(PushResult(token=token, ok=True))
                continue
            details = ticket.get("details") if isinstance(ticket, dict) else None
            error = details.get("error") if isinstance(details, dict) else None
            results.append(
                PushResult(token=token, ok=False, error=str(error) if error else "PUSH_ERROR")
            )
        return results


def build_push_sender() -> PushSender:
    if not settings.expo_push_enabled:
        return NoopPushSender()
    return ExpoPushSender(settings.expo_push_url, settings.expo_access_token)


#: Instancia compartida. Los tests la sustituyen con
#: `monkeypatch.setattr(app.core.push, "push_sender", FakeSender())`.
push_sender: PushSender = build_push_sender()
