"""Envío de avisos al paciente: de la fila `alert` al celular.

Es la capa que une `core/push` (que no sabe de base de datos) con
`patient_app_repository` (que no sabe de Expo). Todo lo que exporta está pensado
para correr desde un `BackgroundTasks`, con su propia sesión.
"""

import uuid

import structlog

from app.core import push as push_module
from app.core.push import PushMessage
from app.db.models.alert import AlertSeverity
from app.modules.patient_app import patient_app_repository as repo

logger = structlog.get_logger(__name__)

#: Solo lo accionable llega al celular. Una alerta `LOW` (ruido de un electrodo
#: que rebotó medio segundo) no justifica despertar a nadie, y la app la muestra
#: igual en Inicio cuando el paciente la abre.
PUSHABLE_SEVERITIES = frozenset({AlertSeverity.HIGH, AlertSeverity.CRITICAL})

_ANOMALY_TITLE = "Registrá cómo te sentís"
_ANOMALY_BODY = (
    "Detectamos algo en tu registro. Contanos qué estabas haciendo: le sirve a tu médico."
)
_VEST_TITLE = "Revisá cómo tenés puesto el chaleco"
_VEST_BODY = "Hace un rato que la señal no llega bien. Acomodátelo para no perder el estudio."


def anomaly_message(alert_id: uuid.UUID, occurred_at_iso: str) -> PushMessage:
    """Aviso de anomalía detectada.

    El `data` es lo que hace que tocar la notificación abra el formulario ya
    anclado a ese momento, en vez de la home.
    """
    return PushMessage(
        title=_ANOMALY_TITLE,
        body=_ANOMALY_BODY,
        data={
            "type": "report_request",
            "alertId": str(alert_id),
            "occurredAt": occurred_at_iso,
        },
    )


def vest_message(alert_id: uuid.UUID, occurred_at_iso: str) -> PushMessage:
    return PushMessage(
        title=_VEST_TITLE,
        body=_VEST_BODY,
        data={
            "type": "vest_misplaced",
            "alertId": str(alert_id),
            "occurredAt": occurred_at_iso,
        },
    )


async def notify_patient_task(patient_id: uuid.UUID, message: PushMessage) -> None:
    """Entrypoint del `BackgroundTasks`: abre su propia sesión.

    La del request ya está cerrada cuando esto corre. Nunca propaga: el trabajo
    real del llamador (ingerir señal, registrar el estado del equipo) ya
    terminó bien y no se puede deshacer porque falló una notificación.
    """
    from app.db.session import async_session_factory

    try:
        async with async_session_factory() as session:
            tokens = await repo.list_push_tokens_for_patient(session, patient_id)
            if not tokens:
                await logger.ainfo("push_no_tokens", patient_id=str(patient_id))
                return
            results = await push_module.push_sender.send(tokens, message)
            dead = [result.token for result in results if result.is_dead_token]
            if dead:
                await repo.deactivate_push_tokens(session, dead)
                await session.commit()
            # Los códigos de error y no solo el conteo: Expo contesta 200 y
            # rechaza mensaje por mensaje, así que un proyecto sin credenciales
            # de FCM cargadas se veía igual que un token vencido —`failed=2` y
            # nada más—, y no había forma de saber que el problema estaba en la
            # consola de Expo y no en este código.
            errors = sorted({result.error for result in results if result.error})
            await logger.ainfo(
                "push_dispatched",
                patient_id=str(patient_id),
                sent=sum(1 for result in results if result.ok),
                failed=sum(1 for result in results if not result.ok),
                deactivated=len(dead),
                errors=errors,
            )
    except Exception:  # noqa: BLE001 — una notificación no puede tumbar la ingesta
        await logger.aexception("push_task_failed", patient_id=str(patient_id))
