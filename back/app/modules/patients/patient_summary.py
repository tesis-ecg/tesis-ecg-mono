"""Resumen del último registro, sin decodificar ni reprocesar el ECG crudo.

La ventana termina en la última muestra recibida, incluso en estudios cerrados.
FC = latidos / minutos analizables, con las exclusiones del informe Holter.
Adherencia = tiempo registrado / tiempo del período (no es un índice de calidad).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ecg_event import ECGEvent, ECGEventValidation
from app.db.models.study import Study
from app.ml.holter_contracts import TimelineRun
from app.modules.patients import patients_repository as repo
from app.modules.patients.patients_schemas import (
    AdherenceMetric,
    EventsMetric,
    HeartRateMetric,
    PatientSummaryInput,
    PatientSummaryOut,
)
from app.modules.studies import studies_repository as study_repo
from app.modules.studies.annotations import (
    SIGNAL_QUALITY_KINDS,
    annotation_category,
    annotation_kind,
)
from app.modules.studies.corrections import doctor_corrections
from app.modules.studies.studies_service import (
    _metric_noise,
    _metric_quality_marks,
    _metric_rails,
    _metric_runs,
)

# Solo guarda dos números por estudio/versión, no señales ni latidos. La clave
# cambia al ingresar señal, avanzar el análisis o corregir/excluir un tramo.
# Se consulta después de comprobar permisos y leer el estado vigente de la DB.
_RATE_CACHE: OrderedDict[str, list[int | None]] = OrderedDict()
_RATE_CACHE_SIZE = 128


async def _cached_heart_rates(
    study: Study, windows: list[SummaryWindow], excluded: list[tuple[int, int]]
) -> list[int | None]:
    payload = json.dumps(
        [
            str(study.id),
            study.sample_rate,
            study.beats_analyzed_samples,
            study.ecg_beat_chunks,
            [w.spans for w in windows],
            sorted(excluded),
        ],
        sort_keys=True,
    )
    key = hashlib.sha256(payload.encode()).hexdigest()
    if key in _RATE_CACHE:
        _RATE_CACHE.move_to_end(key)
        return _RATE_CACHE[key]
    values = await asyncio.to_thread(_heart_rates, study, windows, excluded)
    _RATE_CACHE[key] = values
    _RATE_CACHE.move_to_end(key)
    while len(_RATE_CACHE) > _RATE_CACHE_SIZE:
        _RATE_CACHE.popitem(last=False)
    return values


@dataclass(frozen=True)
class SummaryWindow:
    start_ms: int
    end_ms: int
    # Muestras absolutas, con extremos exclusivos: no comprimir huecos de reloj.
    spans: list[tuple[int, int]]
    recorded_ms: int


def summary_window(runs: list[TimelineRun], start_ms: int, end_ms: int) -> SummaryWindow:
    spans: list[tuple[int, int]] = []
    wall_spans: list[tuple[int, int]] = []
    for run in runs:
        low = max(start_ms, run.start_epoch_ms)
        high = min(end_ms, run.end_epoch_ms)
        duration = run.end_epoch_ms - run.start_epoch_ms
        if high <= low or duration <= 0 or run.sample_count <= 0:
            continue
        # Misma interpolación de muestras/hora que usa el visor y el informe.
        start = run.start_sample + ceil((low - run.start_epoch_ms) * run.sample_count / duration)
        end = run.start_sample + ceil((high - run.start_epoch_ms) * run.sample_count / duration)
        spans.append((start, end))
        wall_spans.append((low, high))
    # Dos arranques con anclas que se solapan no duplican el tiempo registrado.
    recorded_ms = 0
    last_end = start_ms
    for low, high in sorted(wall_spans):
        recorded_ms += max(0, high - max(low, last_end))
        last_end = max(last_end, high)
    return SummaryWindow(start_ms, end_ms, spans, recorded_ms)


def _trend(delta: int | None) -> str:
    return "up" if delta is not None and delta > 0 else "down" if delta else "flat"


def _event_count(events: list[ECGEvent], window: SummaryWindow, rate: int) -> int:
    count = 0
    for event in events:
        if event.validation_status in (ECGEventValidation.REJECTED, ECGEventValidation.NOISE):
            continue
        correction = event.correction or {}
        effective = correction.get("effectiveKind")
        category = annotation_category(event, annotation_kind(event))
        if isinstance(effective, str) and correction.get("effectiveSeverity"):
            category = "signal_quality" if effective in SIGNAL_QUALITY_KINDS else "clinical"
        if effective == "normal_beats" or category != "clinical":
            continue
        sample = round(event.timestamp_in_recording * rate)
        if any(start <= sample < end for start, end in window.spans):
            count += 1
    return count


def _heart_rates(
    study: Study, windows: list[SummaryWindow], excluded: list[tuple[int, int]]
) -> list[int | None]:
    # Imports diferidos: el arranque de la API no debe cargar numpy/scipy.
    import numpy as np

    from app.ml.holter_metrics import MIN_RR_S, _merge_spans, _outside, _overlap
    from app.modules.ingest.processing import load_beats

    rate = study.sample_rate
    limit = study.beats_analyzed_samples
    ranges = [(start, min(end, limit)) for w in windows for start, end in w.spans if start < limit]
    if not ranges:
        return [None] * len(windows)
    # Leer el vecino previo preserva la deduplicación en el borde del período.
    margin = ceil(MIN_RR_S * rate)
    beats = load_beats(study, sample_ranges=[(max(0, s - margin), e) for s, e in ranges])
    samples = np.sort(beats["sample_index"], kind="stable")
    if samples.size:
        samples = samples[np.concatenate(([True], np.diff(samples) >= int(MIN_RR_S * rate)))]
    excluded = _merge_spans(excluded)
    samples = samples[_outside(samples, excluded)]
    values: list[int | None] = []
    for window in windows:
        count = duration = 0
        for start, end in window.spans:
            end = min(end, limit)
            if end <= start:
                continue
            duration += end - start - _overlap(excluded, start, end)
            count += int(np.searchsorted(samples, end) - np.searchsorted(samples, start))
        # Igual que Holter: menos de dos latidos no es evidencia de una FC.
        values.append(round(count * 60 * rate / duration) if duration > 0 and count >= 2 else None)
    return values


async def build_patient_summary(
    input_data: PatientSummaryInput, db: AsyncSession
) -> PatientSummaryOut:
    result = PatientSummaryOut(
        windowHours=input_data.window_hours,
        heartRate=None,
        eventsDetected=None,
        adherencePercent=None,
    )
    study = await repo.get_summary_study(db, input_data.patient_id)
    if study is None:
        return result
    # Una única sesión no admite consultas concurrentes. Cada colección se lee una vez.
    timeline = await study_repo.list_timeline_segments(db, study.id)
    runs = _metric_runs(study, timeline)
    events = await study_repo.list_ecg_events(db, study.id)
    quality = await study_repo.list_quality_intervals(db, study.id)
    first = min(run.start_epoch_ms for run in runs)
    end = max(run.end_epoch_ms for run in runs)
    width = input_data.window_hours * 3_600_000
    current = summary_window(runs, max(first, end - width), end)
    windows = [current]
    # Comparar solo períodos completos de igual duración del mismo estudio.
    if first <= end - 2 * width:
        windows.append(summary_window(runs, end - 2 * width, end - width))
    result.studyId = study.id
    result.windowStart = datetime.fromtimestamp(current.start_ms / 1000, UTC)
    result.windowEnd = datetime.fromtimestamp(current.end_ms / 1000, UTC)
    corrections = doctor_corrections(events)
    exclusions, _ = _metric_quality_marks(events)
    noise = _metric_noise(study, runs, quality)
    # Las mismas reglas y correcciones que /holter-metrics, sin calcular VFC/ST.
    from app.ml.holter_metrics import _subtract_spans

    if noise is not None:
        exclusions += _subtract_spans(_metric_rails(quality), corrections.readable_spans)
        exclusions += _subtract_spans(noise, corrections.readable_spans)
    exclusions += corrections.noise_spans
    rates = await _cached_heart_rates(study, windows, exclusions)
    if rates[0] is not None:
        delta = rates[0] - rates[1] if len(rates) > 1 and rates[1] is not None else None
        result.heartRate = HeartRateMetric(averageBpm=rates[0], deltaBpm=delta, trend=_trend(delta))
    counts: list[int | None] = []
    adherences: list[int | None] = []
    for window in windows:
        count = _event_count(events, window, study.sample_rate)
        analyzed = bool(window.spans) and study.ml_analyzed_samples >= max(
            e for _, e in window.spans
        )
        counts.append(count if count or analyzed else None)
        duration = window.end_ms - window.start_ms
        adherences.append(round(100 * window.recorded_ms / duration) if duration > 0 else None)
    if counts[0] is not None:
        delta = counts[0] - counts[1] if len(counts) > 1 and counts[1] is not None else None
        result.eventsDetected = EventsMetric(count=counts[0], delta=delta, trend=_trend(delta))
    if adherences[0] is not None:
        delta = (
            adherences[0] - adherences[1]
            if len(adherences) > 1 and adherences[1] is not None
            else None
        )
        result.adherencePercent = AdherenceMetric(
            value=adherences[0], deltaPp=delta, trend=_trend(delta)
        )
    return result
