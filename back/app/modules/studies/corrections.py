"""Correcciones del médico sobre los hallazgos, en el formato del recálculo.

Sin numpy, como `holter_contracts`: `studies_service` se importa al arrancar la
API. Acá viven las dos mitades que comparten la ruta de reclasificación y el
cálculo de métricas, para que no puedan discrepar:

- `ReclassificationPlan` / `plan_reclassification`: qué significa una acción del
  médico sobre un hallazgo (clase origen y destino, `kind` efectivo, tramo). Lo
  usa el servicio para **escribir** `ecg_event.correction`.
- `doctor_corrections`: lee **solo** ese `correction` ya congelado y arma
  `DoctorCorrections` para `compute_holter_metrics`. No mira `metadata` ni el
  tipo del evento: si el motor mueve o reescribe el hallazgo después, la
  corrección sigue valiendo sobre el tramo en el que el médico la hizo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.db.models.ecg_event import ECGEvent, ECGEventValidation
from app.ml.holter_contracts import (
    BEAT_LABEL_BY_KIND,
    NOISE_FINDING_KINDS,
    RELABEL_EFFECTIVE_KIND,
    BeatClass,
    BeatRelabel,
    DoctorCorrections,
)
from app.modules.studies.annotations import STUDY_SCOPE, event_scope

ReclassificationAction = Literal["confirm", "reject", "noise", "relabel", "reset"]

#: Veredicto que se guarda por acción (`reset` vuelve a `pending`).
STATUS_BY_ACTION: dict[str, ECGEventValidation] = {
    "confirm": ECGEventValidation.CONFIRMED,
    "reject": ECGEventValidation.REJECTED,
    "noise": ECGEventValidation.NOISE,
    "relabel": ECGEventValidation.RECLASSIFIED,
    "reset": ECGEventValidation.PENDING,
}


class InvalidReclassification(ValueError):
    """La combinación hallazgo/acción/etiqueta no tiene sentido."""


@dataclass(frozen=True)
class ReclassificationPlan:
    """La corrección ya resuelta, tal como se persiste en `ecg_event.correction`."""

    action: str
    from_kind: str
    from_label: BeatClass | None
    to_label: BeatClass | None
    effective_kind: str | None
    span_start: int
    span_end: int
    effective_severity: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "fromKind": self.from_kind,
            "fromLabel": self.from_label,
            "toLabel": self.to_label,
            "effectiveKind": self.effective_kind,
            "effectiveSeverity": self.effective_severity,
            "spanStart": self.span_start,
            "spanEnd": self.span_end,
        }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def event_sample_span(event: ECGEvent) -> tuple[int, int] | None:
    """`[inicio, fin)` del hallazgo en muestras absolutas, o `None` si no se sabe.

    Exige `startSampleIndex` **y** `sampleCount`: sin el largo no hay tramo del
    que re-etiquetar latidos ni que excluir, y adivinarlo (un tramo de una
    muestra) haría que la corrección parezca aplicada sin tocar nada.
    """
    metadata = event.event_metadata or {}
    start = _number(metadata.get("startSampleIndex"))
    count = _number(metadata.get("sampleCount"))
    if start is None or count is None or count <= 0:
        return None
    return int(start), int(start + count)


def is_manual_finding(event: ECGEvent) -> bool:
    return (event.event_metadata or {}).get("source") == "manual"


def is_reclassifiable(event: ECGEvent) -> bool:
    """Si el médico puede corregir el hallazgo: motor o selección manual con tramo.

    Hardware, seeds y disparador de simulación no tienen
    un tramo de señal propio que corregir; los de alcance de estudio abarcan
    horas y no son un tramo. `/findings` lo expone para que el panel no ofrezca
    una corrección que la ruta va a rechazar.
    """
    return (
        (event.model_version is not None or is_manual_finding(event))
        and event_scope(event) != STUDY_SCOPE
        and event_sample_span(event) is not None
    )


def plan_reclassification(
    kind: str,
    span: tuple[int, int],
    action: ReclassificationAction,
    beat_label: BeatClass | None,
    *,
    target_kind: str | None = None,
    severity: str | None = None,
) -> ReclassificationPlan:
    """Resuelve qué significa `action` sobre un hallazgo de `kind`.

    - `confirm`: sin efecto en las métricas; el `kind` efectivo es el propio.
    - `reject`: en los tipos con clase, sus latidos pasan a N; el resto no
      cambia cifras salvo ruido y pausas, que `doctor_corrections` resuelve por
      `fromKind`. No queda `kind` efectivo: el hallazgo ya no se afirma.
    - `noise`: el tramo es Grupo A; no queda `kind` efectivo.
    - `relabel`: acepta tipo y gravedad; el contrato anterior usa `beat_label`.
    """
    own = BEAT_LABEL_BY_KIND.get(kind)
    if target_kind is not None:
        if action != "relabel" or beat_label is not None or severity is None:
            raise InvalidReclassification("kind and severity require relabel without beatLabel")
        # El tipo clínico y la gravedad se guardan sin alterar lo que afirmó el motor.
        target_label: BeatClass = (
            "V" if target_kind == "pvc" else BEAT_LABEL_BY_KIND.get(target_kind, "N")
        )
        return ReclassificationPlan(action, kind, own, target_label, target_kind, *span, severity)
    if severity is not None:
        raise InvalidReclassification("severity requires kind")
    if action == "relabel":
        if own is None or beat_label is None or beat_label == own:
            raise InvalidReclassification("relabel needs a different beat class for this finding")
        effective = RELABEL_EFFECTIVE_KIND[(kind, beat_label)]
        return ReclassificationPlan(action, kind, own, beat_label, effective, *span)
    if beat_label is not None:
        raise InvalidReclassification("beatLabel only applies to relabel")
    if action == "reject":
        to_label: BeatClass | None = "N" if own not in (None, "N") else None
        return ReclassificationPlan(action, kind, own if to_label else None, to_label, None, *span)
    if action == "noise":
        return ReclassificationPlan(action, kind, None, None, None, *span)
    return ReclassificationPlan(action, kind, None, None, kind, *span)


def _span(correction: dict[str, Any]) -> tuple[int, int] | None:
    start = _number(correction.get("spanStart"))
    end = _number(correction.get("spanEnd"))
    if start is None or end is None or end <= start:
        return None
    return int(start), int(end)


def doctor_corrections(events: Sequence[ECGEvent]) -> DoctorCorrections:
    """Las correcciones vigentes, en el orden en que el médico las hizo.

    `reset` no deja nada (`correction` en NULL) y `confirm` no cambia ninguna
    cifra, así que ninguno aparece acá. Los relabels van ordenados por
    `validated_at` para que, si dos hallazgos solapados tocan el mismo latido,
    gane la última decisión.
    """
    noise: list[tuple[int, int]] = []
    readable: list[tuple[int, int]] = []
    rejected_pauses: list[tuple[int, int]] = []
    confirmed_pauses: list[tuple[int, int]] = []
    relabels: list[tuple[float, BeatRelabel]] = []
    corrected = 0
    for event in events:
        correction = event.correction
        if not correction or event.deleted_at is not None:
            continue
        span = _span(correction)
        if span is None:
            continue
        action = correction.get("action")
        when = event.validated_at.timestamp() if event.validated_at is not None else 0.0
        if action == "noise":
            noise.append(span)
            corrected += 1
        elif action == "reject":
            from_kind = correction.get("fromKind")
            if from_kind in NOISE_FINDING_KINDS:
                readable.append(span)
            elif from_kind == "pause":
                rejected_pauses.append(span)
            elif correction.get("fromLabel") and correction.get("toLabel"):
                relabels.append((when, _relabel(span, correction)))
            else:
                continue
            corrected += 1
        elif action == "relabel":
            from_kind = correction.get("fromKind")
            target_kind = correction.get("effectiveKind")
            if target_kind == "pause" and correction.get("effectiveSeverity"):
                confirmed_pauses.append(span)
            # Un cambio de tipo también retira la interpretación anterior.
            if from_kind in NOISE_FINDING_KINDS and target_kind not in NOISE_FINDING_KINDS:
                readable.append(span)
            if from_kind == "pause" and target_kind != "pause":
                rejected_pauses.append(span)
            if target_kind in {
                "noise",
                "lead_off",
                "sqi_unanalyzable",
                "adc_saturated",
                *NOISE_FINDING_KINDS,
            }:
                noise.append(span)
            elif correction.get("toLabel"):
                sources = (
                    [correction["fromLabel"]] if correction.get("fromLabel") else ["N", "S", "V"]
                )
                for source in sources:
                    relabels.append((when, _relabel(span, {**correction, "fromLabel": source})))
            corrected += 1
    relabels.sort(key=lambda item: item[0])
    return DoctorCorrections(
        noise_spans=tuple(noise),
        readable_spans=tuple(readable),
        rejected_pause_spans=tuple(rejected_pauses),
        confirmed_pause_spans=tuple(confirmed_pauses),
        relabels=tuple(relabel for _, relabel in relabels),
        corrected_findings=corrected,
    )


def _relabel(span: tuple[int, int], correction: dict[str, Any]) -> BeatRelabel:
    return BeatRelabel(
        span[0],
        span[1],
        cast(BeatClass, correction["fromLabel"]),
        cast(BeatClass, correction["toLabel"]),
    )
