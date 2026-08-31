"""Carga avisos pendientes de ejemplo para un paciente, para mirar la app móvil.

La bandeja de notificaciones y el contador de Inicio solo se pueblan cuando el
procesamiento de ECG detecta algo, y en local eso pide un estudio entero con
señal sintetizada. Este script salta ese camino y escribe los avisos directo:
cinco **sin responder** más el del chaleco mal colocado, que es el estado que
ejercita la UI completa —el badge del campanita, la pila de "avisos sin
responder" de Inicio y la lista paginada de Notificaciones, con sus dos
tratamientos de color—.

Los avisos cuelgan de `alert.kind` y no de un `ecg_event`, igual que los que
manda el chaleco por `POST /ingest/device-status`. No hay señal detrás: tocar
uno abre el formulario de la bitácora, que es lo que se quiere ver.

Uso (con el stack de docker compose levantado, desde la raíz del repo):

    docker compose exec back python -m app.scripts.seed_patient_alerts
    docker compose exec back python -m app.scripts.seed_patient_alerts --dni 30123456

Fuera de Docker (requiere que DATABASE_URL apunte al host):

    cd back && uv run python -m app.scripts.seed_patient_alerts

Es idempotente: reejecutarlo borra los avisos que dejó la corrida anterior
—los reconoce por su mensaje, que es fijo, junto con las bitácoras que se hayan
cargado respondiéndolos— y vuelve a escribirlos con las marcas de tiempo
corridas a "ahora". No toca ningún aviso real. Está bloqueado fuera de
development/test.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, settings
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.patient import Patient
from app.db.models.patient_report import PatientReport
from app.db.session import async_session_factory
from app.modules.patient_app.alert_actions import (
    VEST_MISPLACED_KIND,
    requires_patient_response,
)

#: El paciente de prueba de la app móvil.
DEFAULT_DNI = "44554402"


@dataclass(frozen=True)
class AlertSpec:
    """Un aviso de ejemplo. `minutes_ago` es lo que la app muestra como "hace…"."""

    kind: str
    severity: AlertSeverity
    message: str
    minutes_ago: int


#: Cinco avisos que piden respuesta, más el del chaleco mal colocado.
#:
#: El del chaleco va aparte de la cuenta a propósito: es el único que no se
#: responde con la bitácora, así que no suma al `pendingTotal` —el badge sigue
#: en 5— pero sí aparece en la lista, que es donde se ve el único aviso que la
#: app pinta en rojo.
#:
#: Las severidades y los tiempos van variados para que se vea la lista con sus
#: distintos tonos y con "hace unos minutos" conviviendo con "ayer".
ALERTS: tuple[AlertSpec, ...] = (
    AlertSpec(
        kind="tachycardia",
        severity=AlertSeverity.HIGH,
        message="Registramos latidos más rápidos de lo habitual durante casi dos minutos.",
        minutes_ago=12,
    ),
    AlertSpec(
        kind="pvc",
        severity=AlertSeverity.MEDIUM,
        message="Detectamos un latido adelantado. Contanos qué estabas haciendo.",
        minutes_ago=95,
    ),
    AlertSpec(
        kind="afib",
        severity=AlertSeverity.CRITICAL,
        message="Detectamos un ritmo irregular. Tu médico ya lo tiene para revisar.",
        minutes_ago=260,
    ),
    AlertSpec(
        kind="bradycardia",
        severity=AlertSeverity.MEDIUM,
        message="Registramos latidos más lentos de lo habitual mientras descansabas.",
        minutes_ago=640,
    ),
    AlertSpec(
        kind="pause",
        severity=AlertSeverity.HIGH,
        message="Hubo una pausa breve en tu ritmo cardíaco. Queremos saber cómo te sentiste.",
        minutes_ago=1_500,
    ),
    AlertSpec(
        kind=VEST_MISPLACED_KIND,
        severity=AlertSeverity.HIGH,
        message="El chaleco está mal colocado y por eso no estamos registrando. Acomodátelo.",
        minutes_ago=40,
    ),
)

#: Mensajes que este script escribió alguna vez y ya no.
#:
#: El borrado se hace por mensaje, así que sin esta lista las filas de una
#: corrida vieja quedarían huérfanas en la base de cualquiera que ya lo hubiera
#: ejecutado. La del síntoma marcado con el botón del chaleco se retiró cuando
#: se decidió que los síntomas se marcan siempre desde la app.
RETIRED_MESSAGES: tuple[str, ...] = (
    "Marcaste un síntoma con el botón del chaleco. Contanos qué sentiste.",
)

#: Los mensajes son la marca que hace idempotente al script: son fijos y solo
#: los escribe este archivo, así que sirven para borrar la corrida anterior sin
#: tocar los avisos de verdad. Si se edita un mensaje, la fila vieja queda
#: huérfana y hay que borrarla a mano.
_MESSAGES = tuple(spec.message for spec in ALERTS) + RETIRED_MESSAGES


async def _patient_by_dni(db: AsyncSession, dni: str) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.dni == dni, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise SystemExit(
            f"No hay ningún paciente activo con DNI {dni}. "
            "Crealo desde el portal médico antes de correr este script."
        )
    return patient


async def seed_alerts(db: AsyncSession, dni: str) -> tuple[Patient, int]:
    if settings.environment not in {Environment.DEVELOPMENT, Environment.TEST}:
        raise SystemExit("Los avisos de ejemplo solo se pueden cargar en development/test.")

    patient = await _patient_by_dni(db, dni)

    previous = Alert.patient_id == patient.id, Alert.message.in_(_MESSAGES)
    # Se cuenta antes de borrar: el `rowcount` del `DELETE` no está tipado en
    # el `Result` async de SQLAlchemy y mypy corre en estricto.
    removed = await db.scalar(select(func.count()).select_from(Alert).where(*previous)) or 0
    # Las bitácoras que se hayan cargado respondiendo a estos avisos se van con
    # ellos. Sin esto, reejecutar el script después de probar el formulario
    # falla con una violación de FK: `patient_report.alert_id` apunta acá.
    await db.execute(
        delete(PatientReport).where(PatientReport.alert_id.in_(select(Alert.id).where(*previous)))
    )
    await db.execute(delete(Alert).where(*previous))

    now = datetime.now(UTC).replace(microsecond=0)
    for spec in ALERTS:
        # El único que puede no pedir respuesta es el del chaleco: cualquier
        # otro que no la pida no aparecería como pendiente, y el sentido de esta
        # seed es justamente ver los cinco sin responder.
        assert requires_patient_response(spec.kind) or spec.kind == VEST_MISPLACED_KIND, spec.kind
        detected_at = now - timedelta(minutes=spec.minutes_ago)
        db.add(
            Alert(
                patient_id=patient.id,
                event_id=None,
                kind=spec.kind,
                severity=spec.severity,
                message=spec.message,
                # La app lee `created_at` como el momento del aviso: es lo que
                # termina en el "hace 12 minutos" de la card.
                created_at=detected_at,
                updated_at=detected_at,
                # Sin leer ni accionar: `seen_at` vacío y sin bitácora asociada.
                seen_at=None,
                acknowledged_at=None,
            )
        )

    await db.commit()
    return patient, removed


async def _run(dni: str) -> None:
    async with async_session_factory() as db:
        patient, removed = await seed_alerts(db, dni)
    if removed:
        print(f"  · borrados {removed} avisos de ejemplo de la corrida anterior")
    pending = sum(1 for spec in ALERTS if requires_patient_response(spec.kind))
    print(
        f"✓ {len(ALERTS)} avisos ({pending} sin responder) para "
        f"{patient.first_name} {patient.last_name} (DNI {patient.dni})."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carga avisos pendientes de ejemplo para ver la app móvil."
    )
    parser.add_argument(
        "--dni",
        default=DEFAULT_DNI,
        help=f"DNI del paciente que recibe los avisos (default: {DEFAULT_DNI}).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.dni))


if __name__ == "__main__":
    main()
