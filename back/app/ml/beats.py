"""Formato persistido de los latidos que detecta el backend.

Un chunk es un arreglo binario de registros `(sample_index, st_mv)` en orden de
muestra. `sample_index` es absoluto sobre el buffer empaquetado del estudio, la
misma coordenada que `ecg_segments`. Es todo lo que necesitan las métricas: el
RR sale de la diferencia entre índices, y la hora de pared de la línea de tiempo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.ml.rpeak_detection import clean_signal, detect_rpeaks
from app.ml.st_analysis import measure_st_levels

BEAT_DTYPE = np.dtype([("sample_index", "<i8"), ("st_mv", "<f4")])
#: Detector que produjo un chunk, en su metadata (`detector`). Un chunk sin la
#: marca es del Pan-Tompkins, anterior a la versión 4 de las métricas: el
#: cálculo se declara con la versión de ese detector (`holter_metrics`).
DETECTOR = "neurokit"


def legacy_chunks(chunks: Sequence[Mapping[str, Any]]) -> bool:
    """`True` si algún chunk de latidos no salió de `DETECTOR`."""
    return any(chunk.get("detector") != DETECTOR for chunk in chunks)


def analyze_window(
    signal_mv: np.ndarray, rate: int, *, offset: int, keep_start: int, keep_end: int
) -> np.ndarray:
    """Latidos de `[keep_start, keep_end)` (absolutos) detectados sobre `signal_mv`.

    `signal_mv` empieza en la muestra absoluta `offset` e incluye contexto a los
    dos lados: los filtros de fase cero y el umbral adaptativo necesitan señal
    antes y después del tramo que se conserva.
    """
    # El detector del motor (NeuroKit) y no el Pan-Tompkins: corrido por lote,
    # el PT reaprendía el umbral cada ~26 s y en un lote entero se enganchaba
    # a la onda T, que daba la FC máxima doble (`INTEGRACION.md`, pendiente
    # 2); y quedaba ciego después de un artefacto, que inventaba pausas (el
    # 3). Sobre las capturas del chaleco da 0,999 / 0,998 contra 0,877 / 0,891
    # (`tools/physionet/DETECTORS.md`), y su umbral es local: el corte por
    # lote no le cambia nada.
    peaks = detect_rpeaks(clean_signal(signal_mv, rate), rate)
    st = measure_st_levels(signal_mv, peaks, rate)
    absolute = peaks + offset
    keep = (absolute >= keep_start) & (absolute < keep_end)
    beats = np.empty(int(keep.sum()), dtype=BEAT_DTYPE)
    beats["sample_index"] = absolute[keep]
    beats["st_mv"] = st[keep]
    return beats


def encode_beats(beats: np.ndarray) -> bytes:
    return np.ascontiguousarray(beats, dtype=BEAT_DTYPE).tobytes()


def decode_beats(payload: bytes) -> np.ndarray:
    if len(payload) % BEAT_DTYPE.itemsize:
        raise ValueError("chunk de latidos con tamaño inválido")
    return np.frombuffer(payload, dtype=BEAT_DTYPE)
