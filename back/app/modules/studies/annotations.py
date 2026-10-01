"""Cómo se traduce un `ecg_event` a algo que el visor puede dibujar.

Vive aparte porque ahora tiene **dos** consumidores: el manifest, que las pinta
sobre la traza, y `/findings`, que las agrupa para la lista del médico. Con la
lógica duplicada, un cambio en el clipping o en la resolución de la categoría
haría que la banda del gráfico y la fila de la lista dejen de coincidir — y esa
es justo la incoherencia que un médico interpreta como que el sistema falla.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.study import Study

AnnotationCategory = Literal["signal_quality", "clinical", "patient_marker", "technical"]
AnnotationSeverity = Literal["low", "medium", "high", "critical"]

SIGNAL_QUALITY_KINDS = {
    "noise",
    "lead_off",
    "sqi_unanalyzable",
    "adc_saturated",
    # Del motor de detección. Caerían bien igual por su `event_type` NOISE; se
    # listan para que este set siga siendo el inventario legible de lo que el
    # sistema sabe decir sobre la calidad de la señal.
    "flatline",
    "noise_burst",
}

CLINICAL_EVENT_TYPES = {
    ECGEventType.TACHYCARDIA,
    ECGEventType.BRADYCARDIA,
    ECGEventType.AFIB,
    ECGEventType.PVC,
    ECGEventType.PAUSE,
    # Sin esto, una anomalía de morfología cae en `technical` y el panel de
    # hallazgos la muestra como detalle de infraestructura en vez de como lo que
    # es: el hallazgo principal que produce el motor.
    ECGEventType.ANOMALY,
}

ANNOTATION_SEVERITY: dict[ECGEventSeverity, AnnotationSeverity] = {
    ECGEventSeverity.LOW: "low",
    ECGEventSeverity.MEDIUM: "medium",
    ECGEventSeverity.HIGH: "high",
    ECGEventSeverity.CRITICAL: "critical",
}

#: Hallazgos que abarcan el estudio entero y **no se dibujan**. Un
#: `recurrent_morphology` va del primer al último latido de su cluster: pintarlo
#: sería una banda de 24 h sobre toda la traza. Su lugar es `/findings`, como
#: encabezado del grupo.
STUDY_SCOPE = "study"


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def event_offsets_ms(event: ECGEvent, study: Study) -> tuple[int, int] | None:
    """Normaliza coordenadas nuevas y legacy al eje comprimido de muestras."""
    metadata: dict[str, Any] = event.event_metadata or {}
    start_sample = finite_number(metadata.get("startSampleIndex"))
    sample_count = finite_number(metadata.get("sampleCount"))
    duration_seconds = finite_number(event.duration_seconds)

    if start_sample is not None:
        start_ms = start_sample * 1000 / study.sample_rate
        if sample_count is not None:
            end_ms = (start_sample + max(sample_count, 0)) * 1000 / study.sample_rate
        else:
            end_ms = start_ms + max(duration_seconds or 0, 0) * 1000
    else:
        offset_seconds = finite_number(metadata.get("offsetInStudySeconds"))
        if offset_seconds is None:
            offset_seconds = finite_number(event.timestamp_in_recording)
        if offset_seconds is None:
            return None
        start_ms = offset_seconds * 1000
        end_ms = start_ms + max(duration_seconds or 0, 0) * 1000

    recording_duration_ms = study.samples_count * 1000 / study.sample_rate
    clipped_start = min(max(start_ms, 0), recording_duration_ms)
    clipped_end = min(max(end_ms, clipped_start), recording_duration_ms)
    return round(clipped_start), round(clipped_end)


def annotation_kind(event: ECGEvent) -> str:
    kind = (event.event_metadata or {}).get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip().lower()
    return event.event_type.value.lower()


def annotation_category(event: ECGEvent, kind: str) -> AnnotationCategory:
    if kind == "symptom_marker":
        return "patient_marker"
    if kind in SIGNAL_QUALITY_KINDS or event.event_type is ECGEventType.NOISE:
        return "signal_quality"
    if event.event_type in CLINICAL_EVENT_TYPES:
        return "clinical"
    return "technical"


def event_scope(event: ECGEvent) -> str:
    scope = (event.event_metadata or {}).get("scope")
    return scope if isinstance(scope, str) else "batch"


@dataclass(frozen=True, slots=True)
class EventView:
    """Un `ecg_event` ya resuelto a coordenadas y vocabulario del visor."""

    event: ECGEvent
    start_ms: int
    end_ms: int
    kind: str
    category: AnnotationCategory
    severity: AnnotationSeverity
    scope: str

    @property
    def id(self) -> uuid.UUID:
        return self.event.id

    @property
    def cluster_id(self) -> int | None:
        value = (self.event.event_metadata or {}).get("clusterId")
        return int(value) if isinstance(value, int) else None

    @property
    def beat_count(self) -> int | None:
        value = (self.event.event_metadata or {}).get("beatCount")
        return int(value) if isinstance(value, int) else None


def event_view(event: ECGEvent, study: Study) -> EventView | None:
    """`None` si el evento no se puede ubicar sobre la señal todavía."""
    offsets = event_offsets_ms(event, study)
    if offsets is None:
        return None
    kind = annotation_kind(event)
    return EventView(
        event=event,
        start_ms=offsets[0],
        end_ms=offsets[1],
        kind=kind,
        category=annotation_category(event, kind),
        severity=ANNOTATION_SEVERITY[event.severity],
        scope=event_scope(event),
    )
