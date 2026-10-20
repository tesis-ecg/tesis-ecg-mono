"""Formato persistido de la clasificación por latido.

Un chunk es un arreglo binario de registros `BEAT_LABEL_DTYPE`, uno por latido
detectado por el motor, en orden de muestra. Es **otro flujo** que el de
`beats.py` (`sample_index, st_mv`), a propósito: aquél lo escribe una pasada que
no tiene el gate de calidad ni el banco de plantillas, y sin ellos no se puede
decir si un latido es normal, supraventricular o ventricular. Las cifras de FC,
VFC, ST y pausas siguen saliendo de `beats.py`; las de S y V, de acá.

Los milisegundos no se guardan, se derivan (`qrs_ms`, `pr_ms`): el formato
queda en muestras y sirve a cualquier frecuencia de muestreo.

Etiquetas (`LABELS`):

- `N` normal.
- `S` supraventricular: prematuro >= 20 % con el QRS angosto y una forma parecida
  a la dominante del paciente.
- `V` ventricular: QRS ancho con una dominante angosta, o forma distinta de la
  dominante.
- `X` en ruido: el R cae en una ventana que el gate de calidad no dio por GOOD.
- `Q` no clasificable: señal GOOD, pero el latido no se pudo extraer ni medir, o
  no pasa la guarda de ruido local.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.ml.contracts import Floats

BEAT_LABEL_FORMAT: Final = 1

#: Quién produjo las etiquetas. Viaja en `ml_state["beatLabels"]` y en la
#: metadata de los hallazgos nuevos. **No** es `PIPELINE_VERSION`: subir esa
#: descarta el banco de plantillas de los estudios en curso
#: (`morphology.bank_from_state`) y renumera sus clusters.
CLASSIFIER_VERSION: Final = "beats-1.0.0"

LABELS: Final = "NSVXQ"
LABEL_CODES: Final = {label: ord(label) for label in LABELS}
CODE_N: Final = ord("N")
CODE_S: Final = ord("S")
CODE_V: Final = ord("V")
CODE_X: Final = ord("X")
CODE_Q: Final = ord("Q")

#: Máscara de `evidence`: por qué el latido tiene la etiqueta que tiene.
EVIDENCE_PREMATURE: Final = 1
EVIDENCE_WIDE: Final = 2
EVIDENCE_MORPH_DIFFERS: Final = 4
EVIDENCE_COMPENSATORY: Final = 8
EVIDENCE_P_DETECTED: Final = 16
EVIDENCE_QRS_MEASURED: Final = 32

#: Marca de "no se midió" en los campos de muestras relativas al R.
NOT_MEASURED: Final = int(np.iinfo(np.int16).min)

BEAT_LABEL_DTYPE: Final = np.dtype(
    [
        ("sample_index", "<i8"),
        ("label", "u1"),
        ("evidence", "u1"),
        ("qrs_onset", "<i2"),
        ("qrs_offset", "<i2"),
        ("p_onset", "<i2"),
        ("qrs_amplitude_mv", "<f4"),
    ]
)

BeatLabelRecords = NDArray[np.void]


@dataclass(frozen=True, slots=True)
class BeatLabelStream:
    """Todas las etiquetas persistidas de un estudio.

    `from_sample` es el inicio del primer bloque clasificado y `until_sample`
    el final del último (`study.ml_analyzed_samples`): fuera de
    `[from_sample, until_sample)` no hay etiquetas. Un estudio analizado en
    parte antes de que existiera el clasificador tiene `from_sample` posterior
    al inicio de su señal, y quien consume tiene que decir que las S y V están
    incompletas en vez de contar ceros.
    """

    records: BeatLabelRecords
    from_sample: int
    until_sample: int
    classifier: str


def empty_records() -> BeatLabelRecords:
    return np.empty(0, dtype=BEAT_LABEL_DTYPE)


def build_records(
    sample_index: NDArray[np.int64],
    label: NDArray[np.uint8],
    evidence: NDArray[np.uint8],
    qrs_onset: NDArray[np.int64],
    qrs_offset: NDArray[np.int64],
    p_onset: NDArray[np.int64],
    qrs_amplitude_mv: Floats,
    *,
    qrs_measured: NDArray[np.bool_],
    p_detected: NDArray[np.bool_],
) -> BeatLabelRecords:
    """Arma los registros. Donde no se midió, el campo queda en `NOT_MEASURED` / NaN."""
    records = np.empty(sample_index.size, dtype=BEAT_LABEL_DTYPE)
    records["sample_index"] = sample_index
    records["label"] = label
    flags = (
        evidence
        | np.where(qrs_measured, EVIDENCE_QRS_MEASURED, 0).astype(np.uint8)
        | np.where(p_detected, EVIDENCE_P_DETECTED, 0).astype(np.uint8)
    )
    records["evidence"] = flags
    records["qrs_onset"] = np.where(qrs_measured, qrs_onset, NOT_MEASURED)
    records["qrs_offset"] = np.where(qrs_measured, qrs_offset, NOT_MEASURED)
    records["p_onset"] = np.where(p_detected, p_onset, NOT_MEASURED)
    records["qrs_amplitude_mv"] = np.where(qrs_measured, qrs_amplitude_mv, np.float32(np.nan))
    return records


def qrs_ms(records: BeatLabelRecords, sample_rate: int) -> Floats:
    """Ancho del QRS en ms; NaN donde no se midió."""
    measured = records["qrs_onset"] != NOT_MEASURED
    width = (records["qrs_offset"].astype(np.int32) - records["qrs_onset"].astype(np.int32)) * (
        1000.0 / sample_rate
    )
    return np.where(measured, width, np.nan).astype(np.float32)


def pr_ms(records: BeatLabelRecords, sample_rate: int) -> Floats:
    """PR en ms (inicio de P a inicio de QRS); NaN donde no hay P o QRS."""
    measured = (records["p_onset"] != NOT_MEASURED) & (records["qrs_onset"] != NOT_MEASURED)
    pr = (records["qrs_onset"].astype(np.int32) - records["p_onset"].astype(np.int32)) * (
        1000.0 / sample_rate
    )
    return np.where(measured, pr, np.nan).astype(np.float32)


def encode_beat_labels(records: BeatLabelRecords) -> bytes:
    return np.ascontiguousarray(records, dtype=BEAT_LABEL_DTYPE).tobytes()


def decode_beat_labels(payload: bytes, *, format: int = BEAT_LABEL_FORMAT) -> BeatLabelRecords:  # noqa: A002
    """Registros de un chunk. `ValueError` si el formato es desconocido o el largo no cierra."""
    if format != BEAT_LABEL_FORMAT:
        raise ValueError(f"formato de etiquetas desconocido: {format}")
    if len(payload) % BEAT_LABEL_DTYPE.itemsize:
        raise ValueError("chunk de etiquetas con tamaño inválido")
    return np.frombuffer(payload, dtype=BEAT_LABEL_DTYPE)
