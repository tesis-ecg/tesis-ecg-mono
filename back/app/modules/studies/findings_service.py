"""`GET /studies/{id}/findings` — la lista que el médico realmente lee.

No es "las anotaciones del manifest otra vez". El manifest responde *dónde* pintar
cada banda sobre la traza; esto responde *qué encontró el sistema*, y esas son dos
preguntas con dos formas distintas:

- **Agrupa.** 412 latidos de la misma morfología son un hallazgo con 412
  ocurrencias. En el manifest son 412 bandas porque cada una está en un lugar
  distinto de la traza; acá serían 412 filas ilegibles.
- **Incluye lo que el manifest excluye.** El encabezado por morfología abarca del
  primer al último latido de su cluster —horas— y por eso no se dibuja. Acá es
  justamente el título del grupo.
- **Dice cuánto no se pudo evaluar.** Un informe que no declara qué fracción del
  Holter era ilegible afirma de más: "no se detectaron arritmias" sobre un
  registro 40 % inutilizable no significa lo mismo que sobre uno limpio.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEventType
from app.db.models.ecg_event import ECGEvent
from app.db.models.signal_quality import SignalQualityInterval, SignalQualityLevel
from app.db.models.study import Study
from app.modules.auth import auth_repository as auth_repo
from app.modules.studies import studies_repository as repo
from app.modules.studies.annotations import STUDY_SCOPE, EventView, event_view
from app.modules.studies.studies_schemas import (
    StudyFindingGroupOut,
    StudyFindingOut,
    StudyFindingsInput,
    StudyFindingsOut,
    StudyQualityIntervalOut,
    StudyQualitySummaryOut,
)

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

#: Hallazgos que no se agrupan: cada uno es un hecho suelto y juntarlos no
#: agrega información. Un marcador de síntoma del paciente es un evento único
#: con su propia hora; apilarlos bajo "marcadores (7)" esconde justamente lo que
#: el médico quiere ver, que es *cuándo*.
_UNGROUPED_KINDS = {"symptom_marker", "internal_gap", "patient_report"}


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": "Estudio no encontrado."},
    )


def _finding_out(view: EventView) -> StudyFindingOut:
    return StudyFindingOut(
        id=view.id,
        kind=view.kind,
        category=view.category,
        severity=view.severity,
        startOffsetMs=view.start_ms,
        endOffsetMs=view.end_ms,
        confidenceScore=view.event.confidence_score,
        modelVersion=view.event.model_version,
        validationStatus=view.event.validation_status.value,
        clusterId=view.cluster_id,
        beatCount=view.beat_count,
    )


def _group_key(view: EventView) -> str:
    """Los episodios de una morfología se agrupan con su encabezado de cluster."""
    if view.cluster_id is not None:
        return f"cluster:{view.cluster_id}"
    return f"kind:{view.kind}"


def _metadata_float(view: EventView, key: str) -> float | None:
    value = (view.event.event_metadata or {}).get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _build_group(key: str, views: list[EventView], items_per_group: int) -> StudyFindingGroupOut:
    header = next((view for view in views if view.scope == STUDY_SCOPE), None)
    items = [view for view in views if view.scope != STUDY_SCOPE]
    reference = header or views[0]
    # Los items se recortan por score, no por orden de aparición: si el médico
    # solo va a mirar diez de cuatrocientos, tienen que ser los diez más
    # atípicos, no los diez primeros de la noche.
    ranked = sorted(
        items,
        key=lambda view: (-(view.event.confidence_score or 0.0), view.start_ms),
    )[:items_per_group]
    ranked.sort(key=lambda view: view.start_ms)
    span = items or views
    return StudyFindingGroupOut(
        key=key,
        kind=reference.kind,
        category=reference.category,
        severity=max((view.severity for view in views), key=lambda value: _SEVERITY_RANK[value]),
        occurrences=len(items),
        beatCount=header.beat_count if header is not None else None,
        burdenPct=_metadata_float(header, "burdenPct") if header is not None else None,
        meanIntraCorrelation=(
            _metadata_float(header, "meanIntraCorrelation") if header is not None else None
        ),
        firstOffsetMs=min(view.start_ms for view in span),
        lastOffsetMs=max(view.end_ms for view in span),
        items=[_finding_out(view) for view in ranked],
    )


def _quality_summary(
    study: Study, intervals: Sequence[SignalQualityInterval]
) -> StudyQualitySummaryOut:
    """Fusiona los intervalos entre lotes y resume la cobertura del registro."""
    merged: list[tuple[int, int, SignalQualityLevel, str]] = []
    for interval in intervals:
        start = interval.start_sample_index
        end = start + interval.sample_count
        if merged and merged[-1][2] is interval.level and merged[-1][1] == start:
            previous = merged[-1]
            # Mismo nivel y contiguo: se funden. El motivo del primero manda —si
            # el tramo pasó de `spectral` a `bsqi` sin dejar de ser malo, lo que
            # el médico necesita saber es que ahí no se pudo leer.
            merged[-1] = (previous[0], end, previous[2], previous[3])
        else:
            merged.append((start, end, interval.level, interval.reason))

    by_level: dict[SignalQualityLevel, int] = {}
    for start, end, level, _ in merged:
        by_level[level] = by_level.get(level, 0) + (end - start)
    evaluated = sum(by_level.values()) or 1

    def ratio(level: SignalQualityLevel) -> float:
        return round(by_level.get(level, 0) / evaluated, 6)

    rate = study.sample_rate or 500
    return StudyQualitySummaryOut(
        analyzableRatio=ratio(SignalQualityLevel.GOOD),
        goodRatio=ratio(SignalQualityLevel.GOOD),
        marginalRatio=ratio(SignalQualityLevel.MARGINAL),
        badRatio=ratio(SignalQualityLevel.BAD),
        evaluatedMs=round(sum(by_level.values()) * 1000 / rate),
        intervals=[
            StudyQualityIntervalOut(
                startOffsetMs=round(start * 1000 / rate),
                endOffsetMs=round(end * 1000 / rate),
                level=level.value,
                reason=reason,
            )
            for start, end, level, reason in merged
        ],
    )


def _model_version(events: Sequence[ECGEvent]) -> str | None:
    for event in events:
        if event.model_version is not None:
            return event.model_version
    return None


async def get_study_findings(input_data: StudyFindingsInput, db: AsyncSession) -> StudyFindingsOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _ = result

    events = await repo.list_ecg_events(db, study.id)
    intervals = await repo.list_quality_intervals(db, study.id)
    views = [view for view in (event_view(event, study) for event in events) if view is not None]

    grouped: dict[str, list[EventView]] = {}
    ungrouped: list[EventView] = []
    totals: dict[str, int] = {}
    for view in views:
        totals[view.kind] = totals.get(view.kind, 0) + 1
        totals[view.category] = totals.get(view.category, 0) + 1
        if view.kind in _UNGROUPED_KINDS:
            ungrouped.append(view)
            continue
        grouped.setdefault(_group_key(view), []).append(view)

    groups = [
        _build_group(key, members, input_data.items_per_group) for key, members in grouped.items()
    ]
    # Orden total y determinístico: hace falta para que el `--check` de OpenAPI y
    # los tests del portal no dependan del orden de llegada de las filas.
    groups.sort(key=lambda group: (-_SEVERITY_RANK[group.severity], -group.occurrences, group.key))
    ungrouped.sort(key=lambda view: (view.start_ms, str(view.id)))

    truncated = any(
        len([view for view in members if view.scope != STUDY_SCOPE]) > input_data.items_per_group
        for members in grouped.values()
    )

    if input_data.actor_id is not None:
        # Misma auditoría que el manifest: expone la misma información clínica
        # del paciente, solo que ordenada de otra manera.
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_ACCESSED,
            user_id=input_data.actor_id,
            metadata={"target_study_id": str(study.id), "protocol": "findings"},
        )
        await db.commit()

    return StudyFindingsOut(
        studyId=study.id,
        sampleRate=study.sample_rate,
        sampleCount=study.samples_count,
        durationMs=round(study.samples_count * 1000 / (study.sample_rate or 500)),
        modelVersion=_model_version(events),
        quality=_quality_summary(study, intervals),
        groups=groups,
        ungrouped=[_finding_out(view) for view in ungrouped],
        totals=totals,
        truncated=truncated,
    )
