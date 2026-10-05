"""La alerta que un lote le va a notificar al paciente.

Vive en un módulo propio porque la producen dos escritores de eventos:
`processing._persist_events` (Capa A y pérdida de señal) y
`ml_persistence.persist_analysis` (el motor). `ml_persistence` no puede importar
`processing` —`processing` ya lo importa a él—, y dos copias de la clase y de la
tabla de rangos terminarían ordenando distinto la misma severidad según quién
escribió el hallazgo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.db.models.ecg_event import ECGEventSeverity

#: Severidades que despiertan al paciente. Una `LOW` (medio segundo de un
#: electrodo que rebotó) no justifica una notificación, y la app la muestra
#: igual en Inicio cuando el paciente la abre.
PUSH_RANK = {ECGEventSeverity.HIGH: 1, ECGEventSeverity.CRITICAL: 2}

_SEVERITY_BY_RANK = {rank: severity for severity, rank in PUSH_RANK.items()}


@dataclass(frozen=True)
class Pushable:
    """La alerta que se va a notificar, con lo que el push necesita saber.

    El `kind` viaja hasta acá porque el título del aviso lo nombra ("tu chaleco
    registró un ritmo irregular") y el formulario que abre lo encabeza. Deducirlo
    después, del lado del push, obligaría a releer el evento con la transacción
    ya cerrada.
    """

    rank: int
    alert_id: uuid.UUID
    kind: str

    @property
    def severity(self) -> ECGEventSeverity:
        """La severidad del hallazgo, que el `rank` ya codifica.

        Viaja en el push para que el formulario que abre se pinte del color del
        aviso. Se deduce del rango en vez de guardarse aparte para que las dos
        cosas no puedan contradecirse.
        """
        return _SEVERITY_BY_RANK[self.rank]


def most_severe(current: Pushable | None, candidate: Pushable | None) -> Pushable | None:
    """La más severa de las dos; ante un empate se queda la que ya estaba.

    Un lote puede traer varias anomalías y se notifica una sola —la más severa—
    para no vaciar la batería del celular ni saturar al paciente con avisos que
    va a terminar silenciando.
    """
    if candidate is None:
        return current
    if current is None or candidate.rank > current.rank:
        return candidate
    return current
