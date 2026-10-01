"""Orquestación del motor de detección. La única entrada desde `processing.py`.

Todo lo de acá es puro: numpy adentro, dataclasses afuera, sin base de datos ni
S3 ni `async`. Esa frontera es lo que permite (a) testear el pipeline entero con
una señal sintética de tres líneas y (b) moverlo a un hilo con **una sola**
llamada, que es lo que hace falta porque analizar un lote de 1 h cuesta ~4-5 s de
CPU y bloquear el event loop de FastAPI ese tiempo congela la API entera.

El orden no es arbitrario, es la estrategia:

    limpiar → R-peaks → GATE DE CALIDAD → ritmo → morfología → episodios

El gate va **antes** que todo lo clínico. Un artefacto de movimiento se parece
muchísimo más a una arritmia que a un latido normal, así que un motor que no
descarta ruido primero produce cientos de falsos positivos por día.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from app.core.config import Settings
from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityLevel
from app.ml import morphology
from app.ml.arrhythmia import detect_rhythm
from app.ml.contracts import (
    EpisodeBudget,
    Finding,
    Flags,
    QualityReport,
    QualityThresholds,
    QualityWindow,
    RhythmThresholds,
    Signal,
)
from app.ml.episodes import RECURRENT_KIND, apply_refractory, enforce_budget, group_beats
from app.ml.hrv import RRSeries, build_rr, hrv_summary, prematurity
from app.ml.morphology import TemplateBank
from app.ml.quality import assess_quality, merge_windows, window_counts
from app.ml.rpeak_detection import (
    clean_signal,
    compensate_firmware_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)

#: Versión del pipeline. Viaja en `ecg_event.model_version` y se **pinnea por
#: estudio** en el primer lote analizado: si el código sube de versión a mitad de
#: un registro, el estudio sigue con el banco viejo. Cambiar de versión a mitad
#: renumeraría clusters y haría mentir a los eventos ya escritos.
PIPELINE_VERSION = "ml-1.0.0"

#: Motivos de la Capa B que sí producen un `ecg_event`. Los de la Capa A
#: (`lead_off`, `saturated`, `firmware_sqi`) los emite `derive_events` desde los
#: bits del hardware y duplicarlos llenaría la traza de bandas repetidas.
_QUALITY_EVENT_KINDS = {
    "flatline": ("flatline", ECGEventSeverity.MEDIUM),
    "spectral": ("noise_burst", ECGEventSeverity.LOW),
    "bsqi": ("noise_burst", ECGEventSeverity.LOW),
    "no_beats": ("noise_burst", ECGEventSeverity.LOW),
}


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    sample_rate: int
    quality: QualityThresholds
    rhythm: RhythmThresholds
    match_threshold: float
    merge_threshold: float
    max_templates: int
    recurrent_min_beats: int
    anomaly_score_min: float
    budget: EpisodeBudget


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Salida del análisis de un lote, con coordenadas **absolutas al estudio**."""

    quality_intervals: tuple[tuple[QualityWindow, int], ...]
    findings: tuple[Finding, ...]
    bank: TemplateBank
    metrics: dict[str, float]
    model_version: str


def build_config(
    settings: Settings, sample_rate: int, *, score_floor: float = 0.0
) -> PipelineConfig:
    """Traduce los settings a los umbrales del motor.

    Es el único lugar donde se convierten unidades: los settings hablan en
    segundos, µV y latidos porque es lo que un cardiólogo puede revisar; el motor
    trabaja en muestras.
    """
    return PipelineConfig(
        sample_rate=sample_rate,
        quality=QualityThresholds(
            window_samples=max(int(settings.ml_quality_window_seconds * sample_rate), 1),
            flatline_mv=settings.ml_flatline_uv / 1000.0,
            psqi_min=settings.ml_quality_psqi_min,
            ksqi_min=settings.ml_quality_ksqi_min,
            bassqi_min=settings.ml_quality_bassqi_min,
            bsqi_min=settings.ml_quality_bsqi_min,
            # ±150 ms: más que el jitter del detector del MCU alrededor de su
            # retardo nominal, menos que el R-R más corto plausible. El retardo
            # en sí no entra acá: se resta antes de comparar.
            bsqi_tolerance_samples=max(int(0.150 * sample_rate), 1),
            firmware_lag_samples=int(settings.ml_firmware_peak_lag_ms * sample_rate / 1000.0),
            firmware_refractory_samples=int(
                settings.ml_firmware_peak_refractory_ms * sample_rate / 1000.0
            ),
        ),
        rhythm=RhythmThresholds(
            tachycardia_bpm=settings.ml_tachycardia_bpm,
            bradycardia_bpm=settings.ml_bradycardia_bpm,
            pause_seconds=settings.ml_pause_seconds,
            min_duration_seconds=settings.ml_rhythm_min_seconds,
        ),
        match_threshold=settings.ml_template_match_threshold,
        merge_threshold=settings.ml_template_merge_threshold,
        max_templates=settings.ml_template_max,
        recurrent_min_beats=settings.ml_recurrent_cluster_min_beats,
        anomaly_score_min=settings.ml_anomaly_score_min,
        budget=EpisodeBudget(
            refractory_seconds=settings.ml_episode_refractory_seconds,
            gap_beats=settings.ml_episode_gap_beats,
            min_beats=settings.ml_episode_min_beats,
            max_per_study=settings.ml_findings_max_per_study,
            max_per_kind=settings.ml_findings_max_per_kind,
            score_floor=score_floor,
        ),
    )


def empty_bank(config: PipelineConfig) -> TemplateBank:
    return TemplateBank(
        model_version=PIPELINE_VERSION, beat_length=morphology.beat_length(config.sample_rate)
    )


def analyze_batch(
    signal_mv: Signal,
    flags: Flags,
    *,
    start_sample_index: int,
    bank: TemplateBank,
    config: PipelineConfig,
    batch_id: str,
    fold_into_bank: bool = True,
    existing_anomalies: int = 0,
) -> PipelineResult:
    """Analiza un lote. **Bloqueante y CPU-bound**: llamar desde un hilo.

    `fold_into_bank` en falso es el camino del reprocesamiento: los latidos se
    puntúan contra el banco actual pero no se suman a sus conteos. Un lote ya
    plegado que se vuelve a plegar duplicaría sus miembros y falsearía la carga
    (`burdenPct`) que lee el médico.
    """
    sample_rate = config.sample_rate
    cleaned = clean_signal(signal_mv, sample_rate)
    firmware_peaks = compensate_firmware_peaks(
        firmware_rpeaks(flags),
        lag_samples=config.quality.firmware_lag_samples,
        refractory_samples=config.quality.firmware_refractory_samples,
    )
    detected_peaks = detect_rpeaks(cleaned, sample_rate)

    report = assess_quality(
        signal_mv,
        flags,
        cleaned,
        firmware_peaks,
        detected_peaks,
        sample_rate=sample_rate,
        thresholds=config.quality,
    )

    rr = build_rr(detected_peaks, report.analyzable, sample_rate)
    findings: list[Finding] = detect_rhythm(rr, config.rhythm, sample_rate)
    findings.extend(_quality_findings(report.windows, sample_rate))

    # --- Etapa 2 ------------------------------------------------------------- #
    beats = morphology.extract_beats(cleaned, detected_peaks, report.analyzable, sample_rate)
    if fold_into_bank and batch_id not in bank.consumed_batch_ids:
        updated_bank, assignment = morphology.assign_and_update(
            bank,
            beats,
            match_threshold=config.match_threshold,
            max_templates=config.max_templates,
            batch_id=batch_id,
            sample_offset=start_sample_index,
        )
    else:
        updated_bank = bank
        assignment = morphology.score_only(bank, beats, match_threshold=config.match_threshold)

    if beats.n_beats:
        beat_prematurity = prematurity(rr)[beats.beat_index]
        # Contra la plantilla DOMINANTE, no contra la asignada: ver
        # `morphology.dissimilarity_to_dominant`.
        scores = morphology.anomaly_score(
            morphology.dissimilarity_to_dominant(updated_bank, beats),
            beat_prematurity,
            match_threshold=config.match_threshold,
        )
        findings.extend(
            group_beats(
                beats.rpeaks,
                scores >= config.anomaly_score_min,
                scores,
                assignment.cluster_ids,
                updated_bank.recurrent_ids(config.recurrent_min_beats),
                sample_rate=sample_rate,
                budget=config.budget,
            )
        )

    # La refractariedad se aplica SOLO a los hallazgos que pueden disparar un
    # aviso al paciente. Sobre morfología produciría bandas fantasma: dos
    # ectópicos separados por 12 s se fusionaban en un hallazgo de 12,5 s con dos
    # latidos adentro, y el visor lo pinta como una banda continua sobre señal
    # que en su enorme mayoría es normal. Para morfología la agregación correcta
    # ya existe y es otra: `gap_beats` dentro del episodio y el hallazgo de
    # estudio por cluster, que cuenta ocurrencias sin mentir sobre la extensión.
    alerting = [item for item in findings if item.event_type is not ECGEventType.ANOMALY]
    morphology_findings = [item for item in findings if item.event_type is ECGEventType.ANOMALY]
    findings = (
        apply_refractory(
            alerting,
            sample_rate=sample_rate,
            refractory_seconds=config.budget.refractory_seconds,
        )
        + morphology_findings
    )
    findings, score_floor = enforce_budget(
        findings, budget=config.budget, existing_anomalies=existing_anomalies
    )

    # Los hallazgos de estudio se agregan DESPUÉS del presupuesto: son un
    # encabezado por morfología, no compiten contra los episodios individuales y
    # tienen su propio techo natural (`max_templates`).
    absolute = [_shift(finding, start_sample_index) for finding in findings]
    absolute.extend(_recurrent_findings(updated_bank, config))

    merged = merge_windows(report.windows)
    intervals = tuple(
        (
            QualityWindow(
                start_sample=interval.start_sample + start_sample_index,
                length_samples=interval.length_samples,
                level=interval.level,
                reason=interval.reason,
                psqi=interval.psqi,
                ksqi=interval.ksqi,
                bassqi=interval.bassqi,
                bsqi=interval.bsqi,
            ),
            window_counts(report.windows, interval),
        )
        for interval in merged
    )

    return PipelineResult(
        quality_intervals=intervals,
        findings=tuple(absolute),
        bank=replace(updated_bank, score_floor=score_floor),
        metrics=_metrics(report, rr, beats.n_beats, updated_bank),
        model_version=PIPELINE_VERSION,
    )


# --------------------------------------------------------------------------- #
# Traducciones
# --------------------------------------------------------------------------- #


def _shift(finding: Finding, offset: int) -> Finding:
    return Finding(
        kind=finding.kind,
        event_type=finding.event_type,
        severity=finding.severity,
        start_sample=finding.start_sample + offset,
        length_samples=finding.length_samples,
        dedupe_key=f"{finding.kind}:{finding.start_sample + offset}"
        if finding.scope == "batch"
        else finding.dedupe_key,
        score=finding.score,
        scope=finding.scope,
        cluster_id=finding.cluster_id,
        beat_count=finding.beat_count,
        alert_message=finding.alert_message,
        metadata=finding.metadata,
    )


def _quality_findings(windows: tuple[QualityWindow, ...], sample_rate: int) -> list[Finding]:
    """Un evento por tramo que la Capa B rechazó y la Capa A había dejado pasar.

    Solo esos: cuando el electrodo está despegado ya hay un `lead_off` de
    `derive_events` sobre el mismo tramo, y pintar dos bandas encima de la misma
    zona no le dice nada nuevo al médico. Lo que sí es información nueva es
    "los electrodos estaban bien y aun así no se pudo leer".
    """
    findings: list[Finding] = []
    for interval in merge_windows(
        [window for window in windows if window.level is SignalQualityLevel.BAD]
    ):
        mapped = _QUALITY_EVENT_KINDS.get(interval.reason)
        if mapped is None:
            continue
        kind, severity = mapped
        findings.append(
            Finding(
                kind=kind,
                event_type=ECGEventType.NOISE,
                severity=severity,
                start_sample=interval.start_sample,
                length_samples=interval.length_samples,
                dedupe_key=f"{kind}:{interval.start_sample}",
                metadata={
                    "reason": interval.reason,
                    "durationSeconds": round(interval.length_samples / sample_rate, 2),
                },
            )
        )
    return findings


def _recurrent_findings(bank: TemplateBank, config: PipelineConfig) -> list[Finding]:
    """Un hallazgo por morfología recurrente, con el conteo acumulado del estudio.

    Es lo que convierte "412 latidos raros" en "una morfología que aparece 412
    veces, el 0,4 % del registro". Se **upsertea** en cada lote, así que el
    contador crece solo.

    La plantilla **dominante no cuenta**: es el latido normal del paciente. Se
    identifica como la de más miembros, que es lo que es por definición — un
    Holter normal tiene más del 90 % de sus latidos en una sola morfología.
    """
    if len(bank.templates) < 2 or bank.beats_seen == 0:
        return []
    dominant = max(bank.templates, key=lambda template: template.count)
    findings: list[Finding] = []
    for template in bank.templates:
        if template.cluster_id == dominant.cluster_id:
            continue
        if template.count < config.recurrent_min_beats:
            continue
        compactness = template.sum_correlation / template.count if template.count else 0.0
        findings.append(
            Finding(
                kind=RECURRENT_KIND,
                event_type=ECGEventType.ANOMALY,
                severity=ECGEventSeverity.MEDIUM,
                start_sample=template.first_sample,
                length_samples=max(template.last_sample - template.first_sample, 1),
                dedupe_key=f"cluster:{template.cluster_id}",
                score=round(min(template.count / max(bank.beats_seen, 1) * 10.0, 1.0), 6),
                scope="study",
                cluster_id=template.cluster_id,
                beat_count=template.count,
                metadata={
                    "burdenPct": round(template.count / bank.beats_seen * 100.0, 4),
                    "meanIntraCorrelation": round(compactness, 6),
                    "beatsSeen": bank.beats_seen,
                },
            )
        )
    return findings


def _metrics(
    report: QualityReport, rr: RRSeries, analyzed_beats: int, bank: TemplateBank
) -> dict[str, float]:
    windows = report.windows
    total = len(windows) or 1
    levels = [window.level for window in windows]
    metrics: dict[str, float] = {
        "windows": float(len(windows)),
        "goodRatio": round(levels.count(SignalQualityLevel.GOOD) / total, 6),
        "marginalRatio": round(levels.count(SignalQualityLevel.MARGINAL) / total, 6),
        "badRatio": round(levels.count(SignalQualityLevel.BAD) / total, 6),
        "analyzedBeats": float(analyzed_beats),
        "templates": float(len(bank.templates)),
        "beatsSeen": float(bank.beats_seen),
        "unmatchedBeats": float(bank.unmatched_beats),
        "firmwarePeaks": 1.0 if report.firmware_peaks_available else 0.0,
    }
    metrics.update(hrv_summary(rr))
    bsqi = [window.bsqi for window in windows if window.bsqi is not None]
    if bsqi:
        metrics["medianBsqi"] = round(float(np.median(bsqi)), 6)
    return metrics
