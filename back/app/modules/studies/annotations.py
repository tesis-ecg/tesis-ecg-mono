"""Cómo se traduce un `ecg_event` a algo que el visor puede dibujar.

Vive aparte porque tiene varios consumidores: el manifest, que las pinta sobre la
traza; la solapa de registros del paciente, que ancla cada respuesta en el
hallazgo que contesta; el informe clínico, que elige las tiras; y `/findings`,
que las agrupa para la lista del médico. Con la lógica duplicada, un cambio en
el clipping, en la hora de pared o en la resolución de la categoría haría que la
banda del gráfico y la fila de la lista dejen de coincidir — y esa es justo la
incoherencia que un médico interpreta como que el sistema falla.

Acá se decide **dónde** cae cada cosa (offset sobre el buffer, hora de pared,
anclaje de los registros). Cómo se serializa para cada pantalla es de cada
consumidor.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, Protocol

from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.patient_report import PatientReport
from app.db.models.study import Study
from app.db.models.study_timeline_segment import StudyTimelineSegment

AnnotationCategory = Literal["signal_quality", "clinical", "patient_marker", "technical"]
AnnotationSeverity = Literal["low", "medium", "high", "critical"]


class WallClockResolver(Protocol):
    """`offsetMs (buffer empaquetado) -> epoch ms (hora real)`. Ver `wall_clock_resolver`.

    `exclusive_end`: el offset es el final **exclusivo** de un rango. Si cae
    justo en el borde de un tramo, se resuelve contra el tramo que termina ahí
    y no contra el siguiente, que empieza después del hueco.
    """

    def __call__(self, offset_ms: int, *, exclusive_end: bool = False) -> int: ...


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


def recorded_ms(study: Study) -> float:
    """Cuánta señal hay grabada, sobre el buffer empaquetado (sin huecos)."""
    return study.samples_count * 1000 / study.sample_rate


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

    recording_duration_ms = recorded_ms(study)
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


def wall_clock_resolver(study: Study, segments: list[StudyTimelineSegment]) -> WallClockResolver:
    """Devuelve `offsetMs (buffer empaquetado) -> epoch ms (hora real)`.

    Los offsets de las anotaciones están sobre el buffer de muestras, que no deja
    huecos. La hora de pared sí los tiene. La traducción es por tramo: se busca
    el que contiene esa muestra y se cuenta desde su hora de inicio.

    Sin tramos —estudio seedeado o legacy, o uno ingerido antes del backfill— se
    cae al comportamiento anterior, que para una grabación sin cortes da lo
    mismo.
    """
    started_ms = int(study.started_at.timestamp() * 1000)
    rate = study.sample_rate or 500

    def resolve(offset_ms: int, *, exclusive_end: bool = False) -> int:
        sample = offset_ms * rate / 1000
        for segment in segments:
            end = segment.start_sample_index + segment.sample_count
            if sample < end or (exclusive_end and sample == end) or segment is segments[-1]:
                return segment_epoch_ms(segment, sample)
        return started_ms + offset_ms

    return resolve


def segment_epoch_ms(segment: StudyTimelineSegment, sample: float) -> int:
    """Hora de pared de una muestra **dentro de su tramo**, con su borde final.

    `wall_clock_resolver` elige el tramo por la muestra, así que el final
    exclusivo de un rango que termina justo donde termina su tramo caería en
    el tramo siguiente, después del hueco, si no se lo pide con
    `exclusive_end`. Quien ya sabe a qué tramo pertenece un rango (los
    intervalos de calidad, que nunca cruzan una corrida) resuelve sus dos
    bordes acá, contra ese tramo.
    """
    within = max(sample - segment.start_sample_index, 0)
    return round(
        segment.start_epoch_ms
        + within * (segment.end_epoch_ms - segment.start_epoch_ms) / max(segment.sample_count, 1)
    )


@dataclass(frozen=True, slots=True)
class EventView:
    """Un `ecg_event` ya resuelto a coordenadas y vocabulario del visor."""

    event: ECGEvent
    #: Sobre el buffer empaquetado de muestras.
    start_ms: int
    end_ms: int
    #: Hora de pared real, resuelta contra la línea de tiempo. Es lo que el visor
    #: usa en el eje: los offsets se despegan de la hora en cuanto hay un hueco.
    start_epoch_ms: int
    end_epoch_ms: int
    kind: str
    category: AnnotationCategory
    severity: AnnotationSeverity
    scope: str

    @property
    def id(self) -> uuid.UUID:
        return self.event.id

    @property
    def drawable(self) -> bool:
        return self.scope != STUDY_SCOPE

    @property
    def cluster_id(self) -> int | None:
        value = (self.event.event_metadata or {}).get("clusterId")
        return int(value) if isinstance(value, int) else None

    @property
    def beat_count(self) -> int | None:
        value = (self.event.event_metadata or {}).get("beatCount")
        return int(value) if isinstance(value, int) else None


def event_view(event: ECGEvent, study: Study, to_epoch_ms: WallClockResolver) -> EventView | None:
    """`None` si el evento no se puede ubicar sobre la señal todavía."""
    offsets = event_offsets_ms(event, study)
    if offsets is None:
        return None
    kind = annotation_kind(event)
    return EventView(
        event=event,
        start_ms=offsets[0],
        end_ms=offsets[1],
        start_epoch_ms=to_epoch_ms(offsets[0]),
        # Un evento que termina justo donde termina su tramo (la cola de una
        # corrida, que el motor analiza hasta su última muestra) no dura todo
        # el hueco de grabación que viene después.
        end_epoch_ms=to_epoch_ms(offsets[1], exclusive_end=offsets[1] > offsets[0]),
        kind=kind,
        category=annotation_category(event, kind),
        severity=ANNOTATION_SEVERITY[event.severity],
        scope=event_scope(event),
    )


def drawable_event_views(
    study: Study, events: list[ECGEvent], to_epoch_ms: WallClockResolver
) -> list[EventView]:
    """Los hallazgos que van sobre la traza, en el orden en que llegaron.

    Los de alcance de estudio quedan afuera: un `recurrent_morphology` abarca del
    primer al último latido de su morfología, o sea horas, y pintarlo sería una
    banda sobre todo el ECG. Se ven en `/findings`, agrupando a los episodios que
    sí están sobre la traza.
    """
    views = (event_view(event, study, to_epoch_ms) for event in events)
    return [view for view in views if view is not None and view.drawable]


def event_offset_map(study: Study, events: list[ECGEvent]) -> dict[uuid.UUID, tuple[int, int]]:
    """Offsets de los hallazgos **dibujables**, indexados por id de evento.

    Vive aparte de las anotaciones armadas porque hay dos consumidores con
    necesidades distintas: el manifest quiere las anotaciones completas, y la
    solapa de registros del paciente solo quiere saber dónde cayó cada hallazgo
    para anclarle su respuesta. Que los dos usen el mismo criterio
    (`event_offsets_ms` + alcance) es lo que garantiza que la solapa y el gráfico
    nunca discrepen sobre dónde está una marca.
    """
    offsets: dict[uuid.UUID, tuple[int, int]] = {}
    for event in events:
        if event_scope(event) == STUDY_SCOPE:
            continue
        resolved = event_offsets_ms(event, study)
        if resolved is not None:
            offsets[event.id] = resolved
    return offsets


# --- Registros del paciente ------------------------------------------------- #


class ReportPlacement(NamedTuple):
    """Coordenada del registro sobre la traza y el hallazgo del que cuelga."""

    #: `None` mientras no haya señal debajo: el registro existe pero no se pinta.
    offset_ms: int | None
    #: El `ecg_event` que este registro responde, si quedó dibujado.
    linked_event_id: uuid.UUID | None


def report_offset_ms(
    report: PatientReport, study: Study, segments: list[StudyTimelineSegment]
) -> int | None:
    """Dónde cae el registro dentro de la señal, o `None` si todavía no hay.

    **No se recorta contra el final de la grabación**, a diferencia de
    `event_offsets_ms`. Ese clipping es correcto para un evento derivado de un
    lote ya decodificado: sus coordenadas vienen en muestras que existen. Acá
    no: el paciente pudo marcar el síntoma a las 14:30 y el chaleco subir esa
    hora recién a las 15:00. Recortarlo pegaría todos los registros recientes
    contra el borde derecho de la traza — una marca en un lugar donde no pasó
    nada, que es peor que no mostrar nada.

    Devolver `None` hace que el registro espere. Cuando llegue el lote,
    `samples_count` crece y la misma función lo empieza a ubicar sola: no hay
    job ni backfill, es una función del estado actual.
    """
    if segments:
        occurred_ms = int(report.occurred_at.timestamp() * 1000)
        for segment in segments:
            if segment.anchor_matches_boot is not True:
                continue
            if segment.start_epoch_ms <= occurred_ms <= segment.end_epoch_ms:
                span = max(segment.end_epoch_ms - segment.start_epoch_ms, 1)
                within = (occurred_ms - segment.start_epoch_ms) * segment.sample_count / span
                sample = segment.start_sample_index + within
                return round(sample * 1000 / study.sample_rate)
        return None
    if not study.started_at_verified:
        return None
    offset_ms = (report.occurred_at - study.started_at).total_seconds() * 1000
    return round(offset_ms) if 0 <= offset_ms <= recorded_ms(study) else None


def answered_event_id(report: PatientReport) -> uuid.UUID | None:
    """El `ecg_event` que originó el aviso que este registro contesta."""
    alert = report.alert
    if alert is None:
        return None
    return alert.event_id


def report_placements(
    study: Study,
    reports: list[PatientReport],
    event_offsets: dict[uuid.UUID, tuple[int, int]],
    segments: list[StudyTimelineSegment],
) -> dict[uuid.UUID, ReportPlacement]:
    """Dónde va cada registro sobre la traza, y de qué hallazgo cuelga.

    Un registro espontáneo se ubica por su hora de pared (`report_offset_ms`).
    Uno que **responde un aviso** se ancla en el medio de la banda del hallazgo
    que contesta, y no en su propia hora:

    - Es lo que el médico necesita ver. Suelta en la traza, una respuesta es
      una marca más entre las 24 h del estudio y no hay forma de saber a qué
      aviso pertenece; sobre la banda, la pertenencia se lee sola.
    - Es lo que hace que exista. El paciente contesta cuando ve la
      notificación, que puede ser media hora después del hallazgo — y esa media
      hora todavía no está grabada, así que por hora de pared el registro
      quedaría "sin señal" y no se pintaría nunca.

    La hora real del registro no se pierde: sigue viajando en `occurredAt` y es
    lo que muestra la solapa de registros.

    `event_offsets` son los hallazgos que **sí** quedaron dibujados. El vínculo
    se resuelve contra ese diccionario y no contra `alert.event_id` a secas:
    anclar contra un hallazgo que el visor no recibió dejaría la respuesta
    colgada de nada.
    """
    placements: dict[uuid.UUID, ReportPlacement] = {}
    for report in reports:
        event_id = answered_event_id(report)
        offsets = event_offsets.get(event_id) if event_id is not None else None
        if offsets is not None and event_id is not None:
            placements[report.id] = ReportPlacement((offsets[0] + offsets[1]) // 2, event_id)
        else:
            placements[report.id] = ReportPlacement(report_offset_ms(report, study, segments), None)
    return placements
