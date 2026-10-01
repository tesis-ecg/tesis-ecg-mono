"""Reglas de ritmo sobre la serie R-R.

Bradicardia, taquicardia y pausa **no son machine learning y no deberían serlo**:
son umbrales sobre intervalos, la definición clínica es explícita y un modelo
acá solo agregaría una caja negra a algo que ya se puede auditar contando.

Lo que sí importa es el gate: las tres reglas corren únicamente sobre intervalos
que la Etapa 1 marcó válidos. Un R-R que cruza un tramo de electrodo despegado
no mide una pausa de cuatro segundos, mide que faltan los latidos que hubo ahí —
y ese es exactamente el falso positivo que hace que un médico deje de confiar en
la herramienta.
"""

from __future__ import annotations

import numpy as np

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml.contracts import Finding, Floats, RhythmThresholds
from app.ml.hrv import RRSeries, instantaneous_bpm
from app.ml.quality import sample_runs

#: Latidos de la mediana móvil sobre la frecuencia. Sin suavizado, una
#: extrasístole con su pausa compensatoria produce un intervalo largo y otro
#: corto seguidos, y dispararía bradicardia y taquicardia sobre el mismo latido.
SMOOTH_BEATS = 8

#: Frecuencias a las que el hallazgo deja de ser "para revisar" y pasa a ser
#: "avisarle al paciente ahora".
TACHYCARDIA_HIGH_BPM = 150.0
BRADYCARDIA_HIGH_BPM = 40.0
#: Una pausa de 3 s o más es un hallazgo crítico en cualquier informe de Holter.
PAUSE_CRITICAL_SECONDS = 3.0

#: Texto del aviso al paciente. Solo los hallazgos severos lo llevan: el push
#: existe para preguntarle cómo se sentía, y una taquicardia de 105 lpm mientras
#: sube una escalera no amerita despertarlo.
_ALERT_MESSAGES = {
    "tachycardia": "Se detectó un episodio de taquicardia sostenida.",
    "bradycardia": "Se detectó un episodio de bradicardia sostenida.",
}


def _smooth_bpm(rr_seconds: Floats) -> Floats:
    bpm = instantaneous_bpm(rr_seconds)
    if bpm.size <= SMOOTH_BEATS:
        return bpm
    padded = np.pad(bpm.astype(np.float64), (SMOOTH_BEATS // 2, SMOOTH_BEATS // 2), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, SMOOTH_BEATS + 1)
    return np.asarray(np.median(windows, axis=-1), dtype=np.float32)[: bpm.size]


def detect_rhythm(rr: RRSeries, thresholds: RhythmThresholds, sample_rate: int) -> list[Finding]:
    """Hallazgos de ritmo con coordenadas **relativas al lote**."""
    if rr.rr_seconds.size == 0:
        return []

    findings: list[Finding] = []
    bpm = _smooth_bpm(rr.rr_seconds)
    valid = rr.valid
    min_samples = int(thresholds.min_duration_seconds * sample_rate)

    for kind, mask, event_type in (
        (
            "tachycardia",
            valid & (bpm > thresholds.tachycardia_bpm),
            ECGEventType.TACHYCARDIA,
        ),
        (
            "bradycardia",
            valid & (bpm < thresholds.bradycardia_bpm),
            ECGEventType.BRADYCARDIA,
        ),
    ):
        for first, count in sample_runs(mask):
            start_sample = int(rr.rpeaks[first])
            end_sample = int(rr.rpeaks[min(first + count, rr.n_beats - 1)])
            length = end_sample - start_sample
            if length < min_samples:
                continue
            segment = bpm[first : first + count]
            extreme = float(np.max(segment) if kind == "tachycardia" else np.min(segment))
            severe = (
                extreme >= TACHYCARDIA_HIGH_BPM
                if kind == "tachycardia"
                else extreme <= BRADYCARDIA_HIGH_BPM
            )
            findings.append(
                Finding(
                    kind=kind,
                    event_type=event_type,
                    severity=ECGEventSeverity.HIGH if severe else ECGEventSeverity.MEDIUM,
                    start_sample=start_sample,
                    length_samples=length,
                    dedupe_key=f"{kind}:{start_sample}",
                    score=None,
                    beat_count=int(count) + 1,
                    alert_message=_ALERT_MESSAGES[kind] if severe else None,
                    metadata={
                        "peakBpm" if kind == "tachycardia" else "minBpm": round(extreme, 2),
                        "medianBpm": round(float(np.median(segment)), 2),
                        "durationSeconds": round(length / sample_rate, 2),
                    },
                )
            )

    # Pausas: un intervalo largo es un hallazgo por sí mismo, no necesita
    # sostenerse. La pausa **es** el evento.
    long_rr = np.flatnonzero(valid & (rr.rr_seconds > thresholds.pause_seconds))
    for index in long_rr:
        seconds = float(rr.rr_seconds[index])
        start_sample = int(rr.rpeaks[index])
        findings.append(
            Finding(
                kind="pause",
                event_type=ECGEventType.PAUSE,
                severity=ECGEventSeverity.CRITICAL
                if seconds >= PAUSE_CRITICAL_SECONDS
                else ECGEventSeverity.HIGH,
                start_sample=start_sample,
                length_samples=int(rr.rpeaks[index + 1]) - start_sample,
                dedupe_key=f"pause:{start_sample}",
                score=None,
                beat_count=2,
                alert_message="Se detectó una pausa en el ritmo.",
                metadata={"pauseSeconds": round(seconds, 3)},
            )
        )

    findings.sort(key=lambda item: item.start_sample)
    return findings
