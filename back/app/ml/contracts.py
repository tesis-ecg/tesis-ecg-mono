"""Tipos compartidos del motor de detección.

Todo `app/ml/` es **numpy adentro, dataclasses afuera**: ni sesión de base, ni
S3, ni `async`. Esa frontera es lo que hace que el pipeline se pueda testear con
una señal sintética de tres líneas y lo que permite moverlo entero a un hilo con
una sola llamada desde `processing.py`.

Los enums de evento sí se importan de `app/db/models`: son `StrEnum` puros y
tener dos vocabularios de severidad —uno del motor y otro de la base— es
exactamente la clase de duplicación que termina en un `KeyError` en el visor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityLevel

#: Señal de una derivación en mV, 1-D. Es lo que produce `decode_batch`.
Signal = NDArray[np.float32]
#: Flags por muestra del firmware (`app/ml/decompression.py`).
Flags = NDArray[np.uint8]
#: Índices de muestra. Siempre int64: un estudio de 24 h a 500 Hz tiene 43 M de
#: muestras y los productos intermedios se van de int32.
Indices = NDArray[np.int64]
Mask = NDArray[np.bool_]
Floats = NDArray[np.float32]

#: Motivo por el que una ventana quedó con el nivel que tiene. Sin esto, "malo"
#: no distingue el electrodo despegado (el paciente lo puede arreglar) del ruido
#: muscular (no lo puede arreglar).
#:
#: Los índices espectrales se reportan por separado (`psqi`, `ksqi`, `bassqi`:
#: el primero que falla, en ese orden) porque fallan por cosas distintas —
#: energía fuera de la banda del QRS, una distribución sin picos, deriva de línea
#: de base— y recalibrar uno a ciegas de los otros no es posible. Las filas
#: viejas pueden traer `spectral`, el motivo único de antes de la separación.
QualityReason = Literal[
    "ok",
    "lead_off",
    "saturated",
    "flatline",
    "psqi",
    "ksqi",
    "bassqi",
    "bsqi",
    "no_beats",
    "firmware_sqi",
]


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    window_samples: int
    flatline_mv: float
    psqi_min: float
    ksqi_min: float
    bassqi_min: float
    bsqi_min: float
    #: Tolerancia del emparejamiento de R-peaks entre detectores.
    bsqi_tolerance_samples: int
    #: Retardo de confirmación del detector del firmware, en muestras. Cero deja
    #: los picos donde llegaron: solo para señal sintética que ya los marca sobre
    #: el pico. Ver `rpeak_detection.compensate_firmware_peaks`.
    firmware_lag_samples: int = 0
    #: Refractario propio sobre los picos del firmware, en muestras.
    firmware_refractory_samples: int = 0
    #: Frecuencia de la red que se quita antes de los índices espectrales. Cero
    #: la apaga: es lo que necesita una señal sintética sin interferencia, y
    #: `build_config` trae los 50 Hz de `ml_mains_hz`. Ver `quality.deinterfere`.
    mains_hz: float = 0.0


@dataclass(frozen=True, slots=True)
class QualityWindow:
    """Una ventana evaluada. Coordenadas **relativas al lote**."""

    start_sample: int
    length_samples: int
    level: SignalQualityLevel
    reason: QualityReason
    psqi: float | None = None
    ksqi: float | None = None
    bassqi: float | None = None
    bsqi: float | None = None


@dataclass(frozen=True, slots=True)
class QualityReport:
    windows: tuple[QualityWindow, ...]
    #: Por muestra: la Etapa 2 solo mira donde esto es verdadero.
    analyzable: Mask
    #: Verdadero si el firmware reportó al menos un R-peak en el lote. Con esto
    #: en falso el bSQI no se calcula: no se puede medir el acuerdo con un
    #: detector que no habló, y aplicarlo igual degradaría todo el registro.
    firmware_peaks_available: bool


@dataclass(frozen=True, slots=True)
class RhythmThresholds:
    tachycardia_bpm: float
    bradycardia_bpm: float
    pause_seconds: float
    min_duration_seconds: float


@dataclass(frozen=True, slots=True)
class EpisodeBudget:
    refractory_seconds: float
    gap_beats: int
    min_beats: int
    max_per_study: int
    max_per_kind: int
    score_floor: float


@dataclass(frozen=True, slots=True)
class Finding:
    """Un hallazgo listo para escribirse como `ecg_event`.

    `start_sample` es **absoluto al estudio**, no al lote: el pipeline traslada
    las coordenadas una sola vez, al final, para que ningún consumidor tenga que
    acordarse de sumar el offset.
    """

    kind: str
    event_type: ECGEventType
    severity: ECGEventSeverity
    start_sample: int
    length_samples: int
    #: Clave natural dentro del estudio. Es lo que hace idempotente el reproceso.
    dedupe_key: str
    #: [0, 1]. Cuán atípico, no una probabilidad calibrada.
    score: float | None = None
    #: `"batch"` se dibuja sobre la traza; `"study"` es un encabezado de grupo
    #: que abarca horas y **se excluye del manifest** — pintaría una banda de
    #: 24 h sobre todo el ECG.
    scope: Literal["batch", "study"] = "batch"
    cluster_id: int | None = None
    beat_count: int | None = None
    alert_message: str | None = None
    metadata: dict[str, float | int | str] = field(default_factory=dict)
    #: El R de cada latido que cuenta `beat_count`, en las mismas coordenadas
    #: que `start_sample`. No se escribe: es lo que deja a la persistencia
    #: sumar **exacto** los latidos de un episodio que se empalma a través de
    #: un borde de bloque —cuenta los R que el evento todavía no cubría— en vez
    #: de estimarlos por el largo, que con latidos agrupados (un foco ectópico
    #: que se acelera) erraba por varios. Vacío en los que no cuentan latidos.
    beat_samples: tuple[int, ...] = ()
