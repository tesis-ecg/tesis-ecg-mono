"""Contratos de las métricas Holter que se usan sin cargar numpy.

`studies_service` los necesita para armar la entrada de `compute_holter_metrics`
(qué eventos excluyen señal, cuáles cortan un RR, los tramos de la línea de
tiempo), y `studies_service` se importa al arrancar la API. `holter_metrics`
carga numpy y scipy a nivel de módulo, y el arranque no los puede pagar
(`test_starting_the_api_does_not_import_numpy`). Por eso viven acá, sin numpy,
y `holter_metrics` los reexporta: quien ya los importaba de ahí no cambia.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tramos cuyos latidos no se cuentan (reglas de `INTEGRACION.md` §4.5).
EXCLUSION_KINDS = frozenset({"lead_off", "sqi_unanalyzable", "adc_saturated"})
#: Eventos que cortan la continuidad: ningún RR cruza uno.
BREAK_KINDS = frozenset(
    {"internal_gap", "backlog_overflow", "missing_frames_inferred", "corrupt_frame"}
)
ECTOPY_UNAVAILABLE = "BEAT_CLASSIFICATION_UNAVAILABLE"


@dataclass(frozen=True)
class TimelineRun:
    """Un tramo continuo del buffer empaquetado con su hora de pared."""

    start_sample: int
    sample_count: int
    start_epoch_ms: int
    end_epoch_ms: int
