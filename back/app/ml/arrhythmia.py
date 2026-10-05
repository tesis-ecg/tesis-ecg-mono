"""Reglas de ritmo sobre la serie R-R.

Bradicardia, taquicardia y pausa **no son machine learning y no deberían serlo**:
son umbrales sobre intervalos, la definición clínica es explícita y un modelo
acá solo agregaría una caja negra a algo que ya se puede auditar contando.

Lo que sí importa es el gate: las tres reglas corren únicamente sobre intervalos
que la Etapa 1 marcó válidos. Un R-R que cruza un tramo de electrodo despegado
no mide una pausa de cuatro segundos, mide que faltan los latidos que hubo ahí —
y ese es exactamente el falso positivo que hace que un médico deje de confiar en
la herramienta.

La excepción es la asistolia larga: deja ventanas enteras sin un QRS, el gate
las rechaza y el R-R que la cruza queda inválido. Esas pausas las agrega, y las
de acá las depura, `quiet_gap.refine_pauses`, que corre después de esto en
`pipeline.analyze_batch`.
"""

from __future__ import annotations

import numpy as np

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml.contracts import Finding, Floats, Mask, RhythmThresholds
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

#: Tramo máximo de R-R **inválidos** que se puentea dentro de un episodio
#: sostenido (`_bridged_runs`): dos ventanas de calidad de 10 s ilegibles
#: seguidas, más el R-R que cruza cada borde a 30 lpm.
#:
#: No es solo para los empalmes del buffer (`frame_gap`, `internal_gap`), que
#: invalidan ~1,4 s. Medido con el simulador de chalecos: el gate rechaza
#: ventanas sueltas en medio de un ritmo limpio —a 155 lpm la curtosis baja de
#: 5 (4,4-4,9, con bSQI = 1,00: los dos detectores ven los mismos latidos), a
#: 40 lpm el basSQI queda en 0,89—, y cada una partía el episodio. Una
#: taquicardia continua de 6 min salía como tres eventos y tres avisos, y de
#: una bradicardia de 2 min quedaban 42 s. Más que esto ya no es una ventana
#: suelta sino señal que no se pudo leer, y el episodio se corta.
MAX_INVALID_BRIDGE_SECONDS = 25.0

#: Texto del aviso al paciente. Solo los hallazgos severos lo llevan: el push
#: existe para preguntarle cómo se sentía, y una taquicardia de 105 lpm mientras
#: sube una escalera no amerita despertarlo.
_ALERT_MESSAGES = {
    "tachycardia": "Se detectó un episodio de taquicardia sostenida.",
    "bradycardia": "Se detectó un episodio de bradicardia sostenida.",
}
#: El de las pausas, que siempre avisan. Lo comparten las de hueco quieto
#: (`quiet_gap.py`): para el paciente es la misma pausa.
PAUSE_ALERT = "Se detectó una pausa en el ritmo."


def _smooth_bpm(rr_seconds: Floats) -> Floats:
    bpm = instantaneous_bpm(rr_seconds)
    if bpm.size <= SMOOTH_BEATS:
        return bpm
    padded = np.pad(bpm.astype(np.float64), (SMOOTH_BEATS // 2, SMOOTH_BEATS // 2), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, SMOOTH_BEATS + 1)
    return np.asarray(np.median(windows, axis=-1), dtype=np.float32)[: bpm.size]


def _bridged_runs(mask: Mask, rr: RRSeries, sample_rate: int) -> list[tuple[int, int, int]]:
    """`sample_runs(mask)` con los tramos cortados solo por R-R inválidos vueltos a unir.

    Un empalme de 50 ms en medio de una taquicardia de 56 s invalida dos o tres
    R-R, y eso partía el episodio en dos mitades de menos de
    `min_duration_seconds` cada una: ninguna llegaba al mínimo, y la
    refractariedad —que las habría fundido— corre después. Se unen dos tramos
    del mismo tipo si **todo** lo que hay entre ellos son R-R inválidos y ese
    hueco no pasa de `MAX_INVALID_BRIDGE_SECONDS`. Un R-R válido que no cumple
    el umbral sí corta: es el ritmo que cambió.

    Cada tramo es `(primero, cantidad, muestras_ilegibles)`: lo último es lo
    que suman los huecos puenteados, para que el mínimo de duración se mida
    sobre lo que sí se leyó (`detect_rhythm`).
    """
    max_gap = MAX_INVALID_BRIDGE_SECONDS * sample_rate
    runs: list[tuple[int, int, int]] = []
    for first, count in sample_runs(mask):
        if runs:
            previous_first, previous_count, unreadable = runs[-1]
            gap_start = previous_first + previous_count
            gap = int(rr.rpeaks[first]) - int(rr.rpeaks[gap_start])
            if not rr.valid[gap_start:first].any() and gap <= max_gap:
                runs[-1] = (previous_first, first + count - previous_first, unreadable + gap)
                continue
        runs.append((first, count, 0))
    return runs


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
        for first, count, unreadable in _bridged_runs(mask, rr, sample_rate):
            start_sample = int(rr.rpeaks[first])
            end_sample = int(rr.rpeaks[min(first + count, rr.n_beats - 1)])
            length = end_sample - start_sample
            # Sostenido es sostenido **leído**: un hueco ilegible une dos tramos
            # del mismo ritmo pero no suma a los 30 s. Dos ráfagas de 15 s con
            # 20 s de ruido en el medio no son una taquicardia de 50 s.
            if length - unreadable < min_samples:
                continue
            # Las frecuencias, solo de los R-R válidos: el que cruza un empalme
            # mide el hueco y no el corazón.
            segment = bpm[first : first + count][valid[first : first + count]]
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
                    beat_samples=tuple(int(peak) for peak in rr.rpeaks[first : first + count + 1]),
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
                alert_message=PAUSE_ALERT,
                beat_samples=(start_sample, int(rr.rpeaks[index + 1])),
                metadata={"pauseSeconds": round(seconds, 3)},
            )
        )

    findings.sort(key=lambda item: item.start_sample)
    return findings
