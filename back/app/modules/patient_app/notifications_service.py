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

# Copy de los avisos.
#
# Tres reglas, y las tres salieron de que el paciente no abría las
# notificaciones anteriores ("Registrá cómo te sentís" no dice de qué se trata):
#
# 1. El título dice **qué pasó**, no qué tiene que hacer. Un aviso que arranca
#    con la tarea se lee como una tarea más del teléfono y se descarta.
# 2. El cuerpo dice **por qué importa** antes del pedido: sin el "tu médico lo
#    necesita para leer tu estudio", contestar parece opcional.
# 3. Cierra con un **call-to-action** en imperativo. Expo no dibuja botones en la
#    notificación, así que el CTA tiene que estar en el texto o no existe.
#
# Y nada de jerga: las etiquetas son las mismas que muestra la app en
# `features/patient/deviceMeta.ts`, para que el aviso y la pantalla que abre
# digan la misma palabra.
_ANOMALY_TITLE_BY_KIND = {
    "tachycardia": "Tu chaleco registró latidos más rápidos de lo habitual",
    "bradycardia": "Tu chaleco registró latidos más lentos de lo habitual",
    "afib": "Tu chaleco registró un ritmo irregular",
    "pvc": "Tu chaleco registró un latido adelantado",
    "pause": "Tu chaleco registró una pausa en el ritmo",
    "noise": "Un tramo de tu registro salió con ruido",
    "symptom_marker": "Marcaste un síntoma en el chaleco",
}
_ANOMALY_TITLE_FALLBACK = "Hay un momento de tu registro para revisar"
_ANOMALY_BODY = (
    "Contanos cómo te sentías en ese momento: tu médico lo necesita para leer tu estudio. "
    "Tocá para responder, es un minuto."
)
_VEST_TITLE = "El chaleco no está registrando bien"
_VEST_BODY = (
    "Hace un rato que la señal llega mal. Tocá para ver cómo acomodarlo: "
    "si sigue así, hoy se pierde el estudio."
)


def anomaly_title(kind: str | None) -> str:
    """Título del aviso, con el hallazgo nombrado en el idioma del paciente."""
    if kind is None:
        return _ANOMALY_TITLE_FALLBACK
    return _ANOMALY_TITLE_BY_KIND.get(kind, _ANOMALY_TITLE_FALLBACK)


def anomaly_message(
    alert_id: uuid.UUID, occurred_at_iso: str, kind: str | None = None
) -> PushMessage:
    """Aviso de anomalía detectada.

    El `data` es lo que hace que tocar la notificación abra el formulario ya
    anclado a ese momento, en vez de la home. El `kind` viaja por la misma vía y
    con el mismo propósito: el formulario lo usa para encabezar con **qué** se
    detectó, que es lo que le permite al paciente reconstruir qué estaba
    haciendo. Un push viejo sin `kind` sigue abriendo el formulario igual.
    """
    data = {
        "type": "report_request",
        "alertId": str(alert_id),
        "occurredAt": occurred_at_iso,
    }
    if kind:
        data["kind"] = kind
    return PushMessage(title=anomaly_title(kind), body=_ANOMALY_BODY, data=data)


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
