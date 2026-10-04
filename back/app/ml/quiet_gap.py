"""Pausas por hueco quieto: la asistolia que el gate de calidad no deja ver.

Una pausa es un R-R largo, y `arrhythmia.detect_rhythm` solo mira los R-R
**válidos**: los que caen enteros en ventanas `good`. Eso falla justo en la
pausa que más importa. Cuando una asistolia dura lo suficiente para que una
ventana de 10 s entera quede sin un QRS, la ventana sale `bad` —casi siempre
por pSQI, kSQI o basSQI, que corren antes que `no_beats`; con ruido nulo, por
`flatline`—, el R-R que la cruza queda inválido y el motor no informaba nada:
ni pausa ni aviso al paciente. Medido sobre 1584 asistolias sintéticas de 3 a
60 s: desde 10 s ninguna salía completa y desde 20 s no salía ninguna pausa.

Abrir el gate no es la solución: existe para que el ruido de un electrodo seco
no se convierta en arritmias, y una ventana con ruido de movimiento no tiene
QRS igual que una asistolia. Lo que separa las dos cosas —medido, no supuesto—
es la amplitud del interior del hueco **relativa a los latidos del propio
paciente**: en una asistolia lo que queda entre los dos R es la línea de base
(y, en un bloqueo AV, las P), a menos de 0,22 veces el QRS; en el ruido del
chaleco que no vetó el hardware, 0,40 o más, y en el de NSTDB, 0,91 o más. Ni
la amplitud absoluta, ni la curtosis, ni la cantidad de picos de NeuroKit
separan nada.

**Qué R son latidos.** La referencia es la amplitud pico a pico (±60 ms sobre
la señal limpia) de los latidos del paciente a ±`REFERENCE_SPAN_S`: los R de
NeuroKit en ventanas `good`, más los que el firmware confirmó en ventanas
`marginal`/bSQI —en un bloqueo AV completo NeuroKit marca cada P y el bSQI deja
todas las ventanas `marginal`, pero los QRS que los dos detectores vieron son
latidos—. Se toma la **población dominante**: la mediana, sin los que miden
menos de `REFERENCE_FLOOR` de ella (P tomadas por R). Con el percentil 90 de
antes, un 10 % de extrasístoles grandes se volvía "el latido del paciente" y
los normales quedaban como hueco quieto. Con eso, un R:

- **acota** una pausa si mide entre `BOUND_MIN` y `BOUND_MAX` de la referencia,
  o si el firmware también lo vio (`tolerance_samples`) y mide al menos
  `GOOD_BEAT_MIN`, sin tope: un escape ventricular chico o enorme que cierra la
  asistolia es un latido aunque no se parezca a los sinusales;
- **corta** el hueco si lo acota, o si es de una ventana `good` donde el
  firmware no arbitró (bloque sin flags) y mide al menos `GOOD_BEAT_MIN`. Donde
  el firmware sí arbitró, un R de NeuroKit que no es creíble ni confirmado no
  corta: las P de un bloqueo AV paroxístico miden hasta 0,15-0,39 del QRS
  (chaleco, MIT-BIH) y partían la asistolia en pedazos que no se informaban.

La regla (`_quiet_gap_pauses`) informa una pausa entre dos R consecutivos de los
que cortan, los dos cotas contra la referencia **del hueco** —la de
`[R1 − 60 s, R1] ∪ [R2, R2 + 60 s]`, no la del centro, que en una asistolia de
dos minutos es el hueco mismo—, cuando:

- **El interior está quieto.** `[R1 + 0,5 s, R2 − 0,3 s]` no tiene nada por
  encima de `QUIET_MAX` veces la referencia (máximo pico a pico local de
  120 ms). Hasta `MAX_EVENTS` transitorios cortos (`EVENT_MAX_S`) que
  sobresalen de lo quieto (`EVENT_CONTRAST`) —un escape que ninguno de los dos
  detectores vio, un pop de electrodo— no anulan el hueco: si tienen la
  pendiente de un latido (`EVENT_BEAT_QRS`) lo parten y se informa cada tramo
  quieto de más de `pause_seconds`; si son una onda lenta —la T tardía de un
  QT largo— quedan adentro del tramo, fuera de lo que se mide. Que en esos
  tramos no hubo latidos es cierto sea lo que sea el transitorio.
- **El contacto no cambió.** La red (lo que `deinterfere` le quitó a la
  señal) y la deriva lenta del interior no superan `MAINS_MAX` y `DRIFT_MAX`
  veces las de las ventanas `good`/`marginal` vecinas. Es lo que separa una
  asistolia de latidos atenuados por pérdida de contacto: en `aviso_ll_ra`
  (43,2-47,3 s) hay 4 s quietos sin R del firmware, con la red 19,6 veces y la
  deriva 7,4 veces más altas que alrededor.
- **El firmware no vio latidos ahí**, si el bloque trae sus `FLAG_R_PEAK`.
- **No falta señal.** Ninguna muestra `LEAD_OFF`, `ADC_SATURATED` o no finita,
  ninguna ventana `lead_off`/`saturated`/`firmware_sqi` y ningún empalme
  (`frame_gap`, `internal_gap`, pérdidas) entre los dos R, ni una ventana
  `flatline` en el interior. Eso es señal que no existe, no latidos que no
  existieron. El borde de una corrida de la línea de tiempo no hace falta
  mirarlo: un bloque nunca la cruza (`processing._pending_blocks`).

**Tramo quieto abierto a la izquierda.** Con el cursor de 300 s, 60 de contexto
y 30 de contexto derecho, una asistolia de más de 90 s puede no tener nunca
sus dos R en la lectura de un mismo bloque. El bloque que ve el R que la
cierra, si su lectura **empieza con contexto** —la misma corrida sigue hacia
atrás—, el R cae después de lo que leyó el bloque anterior y desde el principio
hasta ese R no hay ningún latido, informa la pausa desde el principio de su
lectura (`openStart`): dura por lo menos eso.

La misma medida depura las pausas que el motor ya daba con un R-R válido
(`refine_pauses`): si el interior tiene algo del tamaño de un latido
(`ENGINE_VETO`) **y con la pendiente de un QRS** (`ENGINE_VETO_QRS`, en la
banda `QRS_BAND_HZ`), no es una pausa sino un latido que NeuroKit no vio. En
MIT-BIH eran 46 pausas falsas —41 en el 207— que avisaban al paciente; las 72
verdaderas quedan (interior ≤ 0,16 contra 1,14-2,37 de las falsas; en banda
QRS ≤ 0,10 contra 0,80-1,38). La banda es lo que deja pasar la pausa
post-extrasistólica: la T tardía y grande de la extrasístole que la abre cae
en el interior con 0,7 del QRS de amplitud, pero es lenta.

**Lo que no resuelve.** Un colapso de amplitud a 0,2× o menos sin cambio de
contacto y sin que el firmware vea esos latidos, o una caída de señal del AFE,
no se distinguen de una asistolia con ningún rasgo medido: por amplitud, por
pendiente y por forma, un QRS a 0,2× es una P de un bloqueo AV (en MIT-BIH,
la correlación con la plantilla da 0,76 para unos y 0,77 para otras). Entre
avisar una asistolia real y no inventar una sobre latidos colapsados que nadie
vio, la regla elige lo primero. Una asistolia que sigue hasta el final de la
corrida no tiene R que la cierre: esta regla no informa tramos quietos
abiertos a la derecha. Sin flags del firmware, una P de ventana `good` de más
de `GOOD_BEAT_MIN` sigue cortando el hueco (no hay segundo detector que la
descarte). Y con un QRS tan chico que el firmware no confirma casi ninguno,
todas las ventanas quedan `marginal` sin latidos de referencia.

Todo es puro, como el resto de `app/ml`: numpy adentro, `Finding` afuera, con
coordenadas relativas al lote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d
from scipy.signal import butter, sosfiltfilt

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityLevel
from app.ml.arrhythmia import PAUSE_ALERT, PAUSE_CRITICAL_SECONDS
from app.ml.contracts import (
    Finding,
    Flags,
    Indices,
    Mask,
    QualityReport,
    QualityWindow,
    Signal,
)
from app.ml.quality import invalid_samples, remove_mains

#: Medio ancho de la ventana donde se mide la amplitud de un latido: ±60 ms
#: alrededor del R toma el QRS entero y nada de la P ni de la T.
BEAT_HALF_WIDTH_MS = 60
#: Ventana del pico a pico local del interior. 120 ms es un QRS ancho: lo que
#: tenga forma de latido en el hueco aparece entero en alguna.
ENVELOPE_MS = 120
#: Latidos y ventanas de referencia que entran, a cada lado.
REFERENCE_SPAN_S = 60.0
#: Sin al menos estos latidos de referencia cerca no hay contra qué medir, y la
#: regla no dice nada.
MIN_REFERENCE_BEATS = 5
#: Los latidos de la referencia por debajo de esta fracción de la mediana no
#: entran: son R mal ubicados o P tomadas por R. Contra la mediana y no contra
#: el percentil 90: en MIT-BIH 114 (23 N de 0,38 mV y 5 V de 1,45 mV a ±60 s) el
#: percentil 90 caía en las V, el piso sacaba a todos los N y la referencia era
#: una V; los N del medio quedaban por debajo de `QUIET_MAX` y dos V acotaban
#: una pausa CRITICAL falsa de 12,7 s con diez latidos adentro.
REFERENCE_FLOOR = 0.4
#: Un R acota la pausa si mide entre estas veces la referencia. 0,6 y no 0,5:
#: los verdaderos de MIT-BIH dan 0,63 o más, y la única pausa falsa de la
#: regla en los 48 registros (208, a los 1383,9 s, una caída de señal de 4,8 s)
#: tenía las cotas en 0,55 y 0,51. En las asistolias sintéticas y en las
#: emuladas sobre el chaleco no cambia ninguna. Un R que el firmware confirmó
#: acota con `GOOD_BEAT_MIN` y sin tope.
BOUND_MIN = 0.6
BOUND_MAX = 3.0
#: Un R de una ventana `good` sin arbitraje del firmware corta el hueco salvo
#: que mida menos que esto, y es también el mínimo de un R confirmado. En las 48
#: grabaciones de MIT-BIH hay 3 R verdaderos de ventanas `good` por debajo,
#: contra 272 detecciones falsas.
GOOD_BEAT_MIN = 0.15
#: El interior del hueco: después de la T del primer latido y antes de la P
#: del segundo. Los mismos márgenes rodean a un transitorio del interior.
INTERIOR_PRE_S = 0.5
INTERIOR_POST_S = 0.3
#: Interior quieto. Asistolias: ≤ 0,17 en sintético (con P de bloqueo AV),
#: ≤ 0,22 en MIT-BIH 232; el piso TP de las ventanas buenas del chaleco da p95
#: 0,26. Ruido: ≥ 0,40 en el chaleco, ≥ 0,91 en NSTDB.
QUIET_MAX = 0.30
#: Transitorios que parten el hueco en vez de anularlo. Más, o más largos, ya
#: es un tramo ruidoso: ahí no se puede afirmar nada. Dos tramos fuertes a menos
#: de `EVENT_MERGE_S` son el mismo evento (el QRS y la T de un escape).
MAX_EVENTS = 2
EVENT_MAX_S = 1.0
EVENT_MERGE_S = 0.5
#: Y un transitorio parte el hueco solo si sobresale: lo quieto mide menos que
#: esta fracción del más débil de ellos. Sobre una asistolia, un escape o un pop
#: dan ≤ 0,1. Con latidos atenuados que rondan `QUIET_MAX` —unos apenas arriba,
#: el resto apenas abajo— no sobresale nada: en MIT-BIH 116 y 208, con diez
#: latidos anotados adentro, 0,76 y 0,61.
EVENT_CONTRAST = 0.5
#: Un transitorio es un latido —y entonces acota los tramos de los dos lados—
#: si en la banda del QRS mide al menos esto de la referencia. Una onda lenta no
#: lo es y el tramo la cruza: la T tardía de un QT largo (0,05-0,11) o una de
#: hiperpotasemia (0,21, que todavía acota: acorta la pausa, nunca la inventa)
#: no le roban medio segundo a la pausa que abre su latido. Los escapes anchos
#: de 0,3-1× dan 0,24-0,82, y un pop de 1 mV, 0,30.
EVENT_BEAT_QRS = 0.15
#: Red y deriva del interior contra las ventanas de referencia vecinas.
#: Verdaderos ≤ 1,26 y ≤ 1,70 en MIT-BIH; la pérdida de contacto de
#: `aviso_ll_ra`, 19,6 y 7,4.
MAINS_MAX = 2.5
DRIFT_MAX = 2.5
#: Holguras absolutas de las dos comparaciones: sin red ni deriva en la
#: referencia (red apagada, señal sintética) el cociente no tiene sentido y
#: cualquier residuo numérico lo haría infinito.
MAINS_SLACK_MV = 0.001
DRIFT_SLACK_MV_S = 0.01
#: Corte del pasabajos que define la deriva lenta.
DRIFT_LOWPASS_HZ = 1.0
#: Interior a partir del cual una pausa con R-R válido no es una pausa sino un
#: latido que NeuroKit no vio (`refine_pauses`)...
ENGINE_VETO = 0.6
#: ...siempre que también lo sea en la banda del QRS. Medido en MIT-BIH: las 46
#: falsas dan 0,80-1,38 y las 72 verdaderas ≤ 0,10. La T de 1,2 mV de una
#: extrasístole (σ 90 ms) da ~0,7 de banda ancha y casi nada acá.
QRS_BAND_HZ = (5.0, 20.0)
ENGINE_VETO_QRS = 0.4
#: Lo que se descarta del principio de una lectura antes de mirar un tramo
#: abierto: el arranque de los filtros de `clean_signal` y de `deinterfere`.
OPEN_SETTLE_S = 1.0

_GOOD = SignalQualityLevel.GOOD
_MARGINAL = SignalQualityLevel.MARGINAL
#: Motivos de ventana que son el hardware diciendo que no hay señal.
_HARDWARE_REASONS = frozenset({"lead_off", "saturated", "firmware_sqi"})
#: Motivos de las ventanas `bad` que salen como `noise_burst` (`pipeline`).
_NOISE_REASONS = frozenset({"psqi", "ksqi", "bassqi", "no_beats"})


@dataclass(frozen=True, slots=True)
class GapEvidence:
    """Lo que la regla lee del bloque, ya calculado por `pipeline.analyze_batch`."""

    signal: Signal
    flags: Flags
    #: La señal de `rpeak_detection.clean_signal`, la misma de los R.
    cleaned: Signal
    #: Los R de NeuroKit.
    rpeaks: Indices
    #: Los R del firmware ya compensados (`compensate_firmware_peaks`).
    firmware_peaks: Indices
    report: QualityReport
    #: Ventanas `good` sin el entorno de los empalmes: la máscara de `build_rr`.
    analyzable: Mask
    #: Falso en el entorno de cada empalme (`quality.exclude_splices`).
    splice_free: Mask
    sample_rate: int
    #: La del bSQI: un R de NeuroKit con un R del firmware a esta distancia es un
    #: latido que vieron los dos detectores.
    tolerance_samples: int
    #: Contexto izquierdo del bloque. Mayor que cero, la lectura empieza en medio
    #: de su corrida y un tramo quieto puede venir de antes (`openStart`).
    context_samples: int = 0
    #: Contexto derecho del bloque. El anterior leyó hasta `context_samples +
    #: lookahead_samples` de este: un tramo abierto que cierra antes, ese bloque
    #: ya lo vio entero desde su R.
    lookahead_samples: int = 0


@dataclass(frozen=True, slots=True)
class _Stretch:
    """Un tramo quieto candidato: sus dos cotas y los pedazos de interior que se midieron."""

    first: int
    second: int
    #: `[inicio, fin)` de cada pedazo quieto, sin los transitorios que el tramo cruza.
    parts: tuple[tuple[int, int], ...]
    #: Transitorios que tenía el hueco del que sale el tramo.
    events: int = 0


def _samples(milliseconds: float, sample_rate: int) -> int:
    return max(int(round(milliseconds * sample_rate / 1000.0)), 1)


def _cumulative(mask: Mask) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(mask, dtype=np.int64)))


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values, dtype=np.float64)))) if values.size else 0.0


def _envelope(segment: np.ndarray, size: int) -> np.ndarray:
    """Pico a pico local de `size` muestras, centrado."""
    size = min(size, segment.size)
    high: np.ndarray = maximum_filter1d(segment, size, mode="nearest")
    low: np.ndarray = minimum_filter1d(segment, size, mode="nearest")
    spread: np.ndarray = np.subtract(high, low)
    return spread


def _runs(mask: Mask) -> list[tuple[int, int]]:
    """Los tramos `[inicio, fin)` donde `mask` es verdadero."""
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(
        zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True)
    )


def _near(peaks: np.ndarray, others: np.ndarray, tolerance: int) -> Mask:
    """Por cada pico, si alguno de `others` cae a `tolerance` muestras o menos."""
    if peaks.size == 0 or others.size == 0:
        return np.zeros(peaks.size, dtype=bool)
    others = np.sort(others)
    position = np.searchsorted(others, peaks)
    left = others[np.clip(position - 1, 0, others.size - 1)]
    right = others[np.clip(position, 0, others.size - 1)]
    near: Mask = (np.abs(peaks - left) <= tolerance) | (np.abs(right - peaks) <= tolerance)
    return near


class _Block:
    """Los rasgos de un bloque, calculados una vez y a demanda."""

    def __init__(self, evidence: GapEvidence) -> None:
        self.evidence = evidence
        rate = evidence.sample_rate
        self.n = int(evidence.cleaned.size)
        self.cleaned = evidence.cleaned.astype(np.float64)
        self.peaks = np.asarray(evidence.rpeaks, dtype=np.int64)
        self.span = int(REFERENCE_SPAN_S * rate)
        self.envelope = _samples(ENVELOPE_MS, rate)
        self.beat_width = 2 * _samples(BEAT_HALF_WIDTH_MS, rate)
        self.firmware = np.sort(np.asarray(evidence.firmware_peaks, dtype=np.int64))

        windows = evidence.report.windows
        marginal = self._window_mask(windows, lambda window: window.level is _MARGINAL)
        arbitrated = self._window_mask(windows, lambda window: window.bsqi is not None)
        if self.peaks.size:
            index = np.clip(self.peaks, 0, self.n - 1)
            self.amplitude = self._peak_to_peak(self.cleaned)[index]
            self.good_beat = evidence.analyzable[index]
            self.arbitrated = arbitrated[index]
            self.confirmed = _near(self.peaks, self.firmware, evidence.tolerance_samples)
            # Los latidos del paciente: los `good`, y los de ventanas `marginal`
            # que los dos detectores vieron.
            self.reference_beat = self.good_beat | (
                marginal[index] & evidence.splice_free[index] & self.confirmed
            )
        else:
            self.amplitude = np.empty(0, dtype=np.float64)
            self.good_beat = np.empty(0, dtype=bool)
            self.arbitrated = np.empty(0, dtype=bool)
            self.confirmed = np.empty(0, dtype=bool)
            self.reference_beat = np.empty(0, dtype=bool)

        self.hardware = _cumulative(invalid_samples(evidence.signal, evidence.flags))
        self.hardware_windows = _cumulative(
            self._window_mask(windows, lambda window: window.reason in _HARDWARE_REASONS)
        )
        self.flatline_windows = _cumulative(
            self._window_mask(windows, lambda window: window.reason == "flatline")
        )
        self.splices = _cumulative(~evidence.splice_free)
        #: Las ventanas contra las que se mide el contacto: las que se leen como
        #: un ECG, `good` o `marginal` (pasaron pSQI, kSQI y basSQI).
        self.reference_windows = [
            (window.start_sample, window.start_sample + window.length_samples)
            for window in windows
            if window.level in (_GOOD, _MARGINAL)
        ]
        self.reference_centers = np.array(
            [(start + end) // 2 for start, end in self.reference_windows], dtype=np.int64
        )
        self._contact: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._qrs: tuple[np.ndarray, np.ndarray] | None = None

    def _window_mask(
        self, windows: tuple[QualityWindow, ...], keep: Callable[[QualityWindow], bool]
    ) -> Mask:
        mask = np.zeros(self.n, dtype=bool)
        for window in windows:
            if keep(window):
                mask[window.start_sample : window.start_sample + window.length_samples] = True
        return mask

    def _peak_to_peak(self, values: np.ndarray) -> np.ndarray:
        """Pico a pico de ±`BEAT_HALF_WIDTH_MS` en cada muestra."""
        return _envelope(values, self.beat_width)

    @staticmethod
    def marked(cumulative: np.ndarray, start: int, end: int) -> bool:
        """Si hay alguna muestra marcada en `[start, end)`."""
        return bool(cumulative[max(start, 0)] < cumulative[max(end, 0)])

    def reference_beats(self, spans: tuple[tuple[int, int], ...]) -> np.ndarray | None:
        """Índices de los latidos de referencia de `spans` (`[desde, hasta]`), o None.

        La población dominante: sin los que miden menos de `REFERENCE_FLOOR`
        de la mediana. Sin `MIN_REFERENCE_BEATS` no hay referencia.
        """
        chosen: list[np.ndarray] = []
        for low, high in spans:
            first = int(np.searchsorted(self.peaks, low, side="left"))
            last = int(np.searchsorted(self.peaks, high, side="right"))
            chosen.append(np.arange(first, last)[self.reference_beat[first:last]])
        index = np.unique(np.concatenate(chosen)) if chosen else np.empty(0, dtype=np.int64)
        if index.size < MIN_REFERENCE_BEATS:
            return None
        values = self.amplitude[index]
        index = index[values >= REFERENCE_FLOOR * float(np.median(values))]
        if index.size < MIN_REFERENCE_BEATS:
            return None
        return index

    def reference(self, spans: tuple[tuple[int, int], ...]) -> float | None:
        """Amplitud típica de los latidos del paciente en `spans`."""
        index = self.reference_beats(spans)
        if index is None:
            return None
        median = float(np.median(self.amplitude[index]))
        return median if median > 0 else None

    def gap_reference(self, first: int, second: int) -> tuple[float, float] | None:
        """`(amplitud, banda QRS)` de referencia de un hueco, o None.

        La de amplitud, contra la que se miden las cotas y el interior; la de
        banda, el pico a pico en `QRS_BAND_HZ` de los mismos latidos, contra la
        que se mide si algo del interior tiene la pendiente de un QRS.
        """
        index = self.reference_beats(self.gap_spans(first, second))
        if index is None:
            return None
        amplitude = float(np.median(self.amplitude[index]))
        if amplitude <= 0:
            return None
        _, band_amplitude = self._qrs_band()
        band = float(np.median(band_amplitude[np.clip(self.peaks[index], 0, self.n - 1)]))
        return amplitude, band

    def gap_spans(self, first: int, second: int) -> tuple[tuple[int, int], ...]:
        """Lo que rodea un hueco: `REFERENCE_SPAN_S` antes de su primer R y
        después del segundo. Nunca su centro, que en un hueco largo es el hueco."""
        return ((first - self.span, first), (second, second + self.span))

    def bounds(self, index: int, reference: float) -> bool:
        """Si el R `index` puede cerrar un hueco medido contra `reference`."""
        ratio = float(self.amplitude[index]) / reference
        return BOUND_MIN <= ratio <= BOUND_MAX or (
            bool(self.confirmed[index]) and ratio >= GOOD_BEAT_MIN
        )

    def interior(self, first: int, second: int) -> tuple[int, int]:
        rate = self.evidence.sample_rate
        return first + int(INTERIOR_PRE_S * rate), second - int(INTERIOR_POST_S * rate)

    def _qrs_band(self) -> tuple[np.ndarray, np.ndarray]:
        """La señal limpia en `QRS_BAND_HZ` y su pico a pico de latido. Una vez por bloque."""
        if self._qrs is None:
            rate = self.evidence.sample_rate
            sos = butter(2, QRS_BAND_HZ, btype="band", fs=float(rate), output="sos")
            if self.n > 3 * (2 * sos.shape[0] + 1):
                band = sosfiltfilt(sos, self.cleaned)
            else:
                band = np.zeros(self.n, dtype=np.float64)
            self._qrs = (band, self._peak_to_peak(band))
        return self._qrs

    def beat_inside(self, first: int, second: int) -> bool:
        """Si el interior de un R-R tiene un latido que NeuroKit no vio: del
        tamaño de uno (`ENGINE_VETO`) y con su pendiente (`ENGINE_VETO_QRS`)."""
        start, end = self.interior(first, second)
        if end <= start:
            return False
        references = self.gap_reference(first, second)
        if references is None:
            return False
        reference, band_reference = references
        if float(np.max(_envelope(self.cleaned[start:end], self.envelope))) < (
            ENGINE_VETO * reference
        ):
            return False
        if band_reference <= 0:
            return True
        band = self._qrs_band()[0]
        loudest = float(np.max(_envelope(band[start:end], self.envelope)))
        return loudest >= ENGINE_VETO_QRS * band_reference

    def stretches(
        self, first: int, second: int, start: int, end: int, references: tuple[float, float]
    ) -> list[_Stretch] | None:
        """Los tramos quietos de un hueco, partido en sus transitorios; None si no lo está.

        `[start, end)` es el interior y `references`, las de `gap_reference`.
        Lo que pasa `QUIET_MAX` es un transitorio; los que distan menos de
        `EVENT_MERGE_S` son uno. Más de `MAX_EVENTS`, uno de más de
        `EVENT_MAX_S` o uno que no sobresale de lo quieto (`EVENT_CONTRAST`) es
        un tramo ruidoso. Los que son latidos (`EVENT_BEAT_QRS`) acotan; el
        resto queda adentro del tramo, fuera de lo que se mide.
        """
        rate = self.evidence.sample_rate
        reference, band_reference = references
        envelope = _envelope(self.cleaned[start:end], self.envelope)
        loud = envelope >= QUIET_MAX * reference
        if not loud.any():
            return [_Stretch(first, second, ((start, end),))]
        events: list[tuple[int, int]] = []
        for low, high in _runs(loud):
            if events and low - events[-1][1] < int(EVENT_MERGE_S * rate):
                events[-1] = (events[-1][0], high)
            else:
                events.append((low, high))
        if len(events) > MAX_EVENTS or any(
            high - low > int(EVENT_MAX_S * rate) for low, high in events
        ):
            return None
        # Lo quieto entre transitorio y transitorio, con los márgenes de un latido.
        before, after = int(INTERIOR_POST_S * rate), int(INTERIOR_PRE_S * rate)
        edges = [start, *(start + low - before for low, _ in events)]
        resumes = [start + high + after for _, high in events]
        parts = list(zip([start, *resumes], [*edges[1:], end], strict=True))
        quiet = max(
            (
                float(np.max(_envelope(self.cleaned[low:high], self.envelope)))
                for low, high in parts
                if high > low
            ),
            default=0.0,
        )
        if quiet >= EVENT_CONTRAST * min(float(np.max(envelope[a:b])) for a, b in events):
            return None
        # Cada latido se ubica donde es más empinado: en la banda del QRS la
        # deriva lenta no cuenta, y lo que domina es su componente más rápida.
        band = self._qrs_band()[0][start:end]
        band_envelope = _envelope(band, self.envelope)
        anchors = [first]
        groups: list[list[tuple[int, int]]] = [[]]
        for part, (low, high) in zip(parts, events, strict=False):
            groups[-1].append(part)
            if float(np.max(band_envelope[low:high])) >= EVENT_BEAT_QRS * band_reference:
                anchors.append(start + low + int(np.argmax(np.abs(band[low:high]))))
                groups.append([])
        groups[-1].append(parts[-1])
        anchors.append(second)
        stretches: list[_Stretch] = []
        for index, group in enumerate(groups):
            kept = tuple((low, high) for low, high in group if high > low)
            if kept:
                stretches.append(
                    _Stretch(anchors[index], anchors[index + 1], kept, events=len(events))
                )
        return stretches

    def _contact_features(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Red y deriva por muestra, y su valor en cada ventana de referencia. Una vez por bloque.

        La red es lo que `deinterfere` le quitó a la señal sin media (cero en
        las muestras inválidas, que dejó crudas). La deriva es la pendiente
        absoluta, en mV/s, de la señal sin media pasada por un pasabajos de
        `DRIFT_LOWPASS_HZ`, con las inválidas puenteadas antes de filtrar: el
        riel de un electrodo despegado no puede contaminar la deriva de las
        ventanas buenas de al lado.
        """
        if self._contact is None:
            evidence = self.evidence
            rate = evidence.sample_rate
            centered = remove_mains(evidence.signal, rate, 0.0).astype(np.float64)
            mains = centered - evidence.report.deinterfered.astype(np.float64)
            invalid = invalid_samples(evidence.signal, evidence.flags)
            bridged = centered.copy()
            valid_positions = np.flatnonzero(~invalid)
            invalid_positions = np.flatnonzero(invalid)
            if valid_positions.size and invalid_positions.size:
                bridged[invalid_positions] = np.interp(
                    invalid_positions, valid_positions, centered[valid_positions]
                )
            sos = butter(2, DRIFT_LOWPASS_HZ, btype="low", fs=float(rate), output="sos")
            # El `padlen` por omisión de `sosfiltfilt`: más corta no se puede filtrar.
            if bridged.size > 3 * (2 * sos.shape[0] + 1):
                slow = sosfiltfilt(sos, bridged)
                drift = np.abs(np.gradient(slow)) * rate
            else:
                drift = np.zeros(bridged.size, dtype=np.float64)
            window_mains = np.array([_rms(mains[a:b]) for a, b in self.reference_windows])
            window_drift = np.array(
                [float(np.percentile(drift[a:b], 95)) for a, b in self.reference_windows]
            )
            self._contact = (mains, drift, window_mains, window_drift)
        return self._contact

    def contact_reference(self, first: int, second: int) -> tuple[float, float] | None:
        """`(red, deriva)` de referencia del hueco, o None.

        Las medianas de las ventanas de referencia con el centro a
        `REFERENCE_SPAN_S` o menos antes de su primer R o después del segundo.
        Sin ninguna no hay contra qué comparar y la regla no informa.
        """
        if not self.reference_windows:
            return None
        centers = self.reference_centers
        near = ((centers >= first - self.span) & (centers <= first)) | (
            (centers >= second) & (centers <= second + self.span)
        )
        if not near.any():
            return None
        _, _, window_mains, window_drift = self._contact_features()
        return float(np.median(window_mains[near])), float(np.median(window_drift[near]))

    def contact(self, parts: tuple[tuple[int, int], ...]) -> tuple[float, float]:
        """`(red, deriva)` de los pedazos `[inicio, fin)` de un tramo."""
        mains, drift, _, _ = self._contact_features()
        mains_values = np.concatenate([mains[low:high] for low, high in parts])
        drift_values = np.concatenate([drift[low:high] for low, high in parts])
        return _rms(mains_values), float(np.percentile(drift_values, 95))


def _pause(
    first: int, second: int, sample_rate: int, metadata: dict[str, float | int | str]
) -> Finding:
    seconds = (second - first) / sample_rate
    return Finding(
        kind="pause",
        event_type=ECGEventType.PAUSE,
        severity=ECGEventSeverity.CRITICAL
        if seconds >= PAUSE_CRITICAL_SECONDS
        else ECGEventSeverity.HIGH,
        start_sample=first,
        length_samples=second - first,
        dedupe_key=f"pause:{first}",
        score=None,
        beat_count=2,
        alert_message=PAUSE_ALERT,
        beat_samples=(first, second),
        metadata={"pauseSeconds": round(seconds, 3), **metadata},
    )


def _classify(block: _Block) -> tuple[Mask, Mask]:
    """`(acota, corta)` por cada R de NeuroKit, contra su referencia local."""
    ratios = np.full(block.peaks.size, np.nan)
    for position, peak in enumerate(block.peaks.tolist()):
        reference = block.reference(((peak - block.span, peak + block.span),))
        if reference is not None:
            ratios[position] = float(block.amplitude[position]) / reference
    known = ~np.isnan(ratios)
    filled = np.where(known, ratios, 0.0)
    credible = known & (filled >= BOUND_MIN) & (filled <= BOUND_MAX)
    # Sin referencia no se puede decir que es chico: cuenta.
    big_enough = ~known | (filled >= GOOD_BEAT_MIN)
    bound = credible | (block.confirmed & big_enough)
    cut = bound | (block.good_beat & ~block.arbitrated & big_enough)
    return bound, cut


def _quiet_gap_pauses(block: _Block, *, pause_seconds: float) -> list[Finding]:
    """Las pausas por hueco quieto del bloque, en coordenadas relativas al lote."""
    evidence = block.evidence
    rate = evidence.sample_rate
    peaks = block.peaks
    if peaks.size == 0:
        return []
    bound, cut = _classify(block)
    minimum = pause_seconds * rate
    cuts = np.flatnonzero(cut).tolist()

    # Los pares de R consecutivos que cortan y, si la lectura empieza en medio
    # de su corrida, el tramo abierto hasta el primero (`None` = sin R que abra).
    candidates: list[tuple[int | None, int]] = list(zip(cuts[:-1], cuts[1:], strict=True))
    if (
        evidence.context_samples > 0
        and cuts
        and int(peaks[cuts[0]]) >= evidence.context_samples + evidence.lookahead_samples
    ):
        candidates.insert(0, (None, cuts[0]))

    found: list[Finding] = []
    for index_first, index_second in candidates:
        if not bound[index_second] or (index_first is not None and not bound[index_first]):
            continue
        second = int(peaks[index_second])
        first = 0 if index_first is None else int(peaks[index_first])
        if second - first <= minimum:
            continue
        start, end = block.interior(first, second)
        if index_first is None:
            start = int(OPEN_SETTLE_S * rate)
        if end <= start:
            continue
        # Señal que falta: nunca se infiere una pausa a través de ella.
        if (
            block.marked(block.hardware, first, second + 1)
            or block.marked(block.hardware_windows, first, second + 1)
            or block.marked(block.splices, first, second + 1)
            or block.marked(block.flatline_windows, start, end)
        ):
            continue
        firmware = block.firmware
        if firmware.size and bool(((firmware >= start) & (firmware < end)).any()):
            continue
        # Todo contra la referencia del hueco: las cotas, el interior y el contacto.
        references = block.gap_reference(first, second)
        if references is None:
            continue
        reference = references[0]
        if not block.bounds(index_second, reference) or (
            index_first is not None and not block.bounds(index_first, reference)
        ):
            continue
        stretches = block.stretches(first, second, start, end, references)
        contact_reference = block.contact_reference(first, second)
        if stretches is None or contact_reference is None:
            continue
        for stretch in stretches:
            if stretch.second - stretch.first <= minimum:
                continue
            pause = _stretch_pause(
                block,
                stretch,
                reference,
                contact_reference,
                opened=index_first is None and stretch.first == first,
            )
            if pause is not None:
                found.append(pause)
    return found


def _stretch_pause(
    block: _Block,
    stretch: _Stretch,
    reference: float,
    contact_reference: tuple[float, float],
    *,
    opened: bool,
) -> Finding | None:
    """La pausa de un tramo quieto, o None si el contacto cambió en él."""
    mains_reference, drift_reference = contact_reference
    mains, drift = block.contact(stretch.parts)
    if mains > MAINS_MAX * mains_reference + MAINS_SLACK_MV:
        return None
    if drift > DRIFT_MAX * drift_reference + DRIFT_SLACK_MV_S:
        return None
    quiet = max(
        float(np.max(_envelope(block.cleaned[low:high], block.envelope)))
        for low, high in stretch.parts
    )
    metadata: dict[str, float | int | str] = {
        "quietGap": True,
        "interiorRatio": round(quiet / reference, 3),
        "lastBeatRatio": round(_anchor_amplitude(block, stretch.second) / reference, 3),
    }
    if opened:
        # No hay R que abra: lo que se sabe es que desde ahí no hubo latidos.
        metadata["openStart"] = True
    else:
        metadata["firstBeatRatio"] = round(_anchor_amplitude(block, stretch.first) / reference, 3)
    if stretch.events:
        metadata["interiorEvents"] = stretch.events
    if mains_reference > 0:
        metadata["mainsRatio"] = round(mains / mains_reference, 3)
    if drift_reference > 0:
        metadata["driftRatio"] = round(drift / drift_reference, 3)
    return _pause(stretch.first, stretch.second, block.evidence.sample_rate, metadata)


def _anchor_amplitude(block: _Block, sample: int) -> float:
    """Pico a pico de ±`BEAT_HALF_WIDTH_MS` alrededor de una cota."""
    half = block.beat_width // 2
    segment = block.cleaned[max(sample - half, 0) : sample + half + 1]
    return float(segment.max() - segment.min()) if segment.size else 0.0


def _within(inner: Finding, outer: Finding) -> bool:
    return (
        outer.start_sample <= inner.start_sample
        and inner.start_sample + inner.length_samples <= outer.start_sample + outer.length_samples
    )


def refine_pauses(
    findings: list[Finding], evidence: GapEvidence, *, pause_seconds: float
) -> list[Finding]:
    """Los hallazgos de ritmo con las pausas depuradas y las de hueco quieto agregadas.

    Tres cosas, antes de la refractariedad:

    1. Las pausas de hueco quieto (`_quiet_gap_pauses`).
    2. Una pausa del motor que cae adentro de una de hueco quieto más larga se
       reemplaza: es la misma asistolia partida por un pico falso de NeuroKit,
       con la duración —y quizás la severidad— mal.
    3. Una pausa del motor con un latido adentro (`_Block.beat_inside`) se
       descarta. Sin referencia contra la cual medir, queda como estaba.

    Y una de hueco quieto que cae adentro de una pausa del motor que quedó
    —la misma, o un tramo de ella— no se agrega: el motor ya la vio con un
    R-R válido.
    """
    block = _Block(evidence)
    quiet = _quiet_gap_pauses(block, pause_seconds=pause_seconds)
    engine: list[Finding] = []
    for finding in findings:
        if finding.kind != "pause":
            continue
        if any(_within(finding, item) and not _within(item, finding) for item in quiet):
            continue
        if block.beat_inside(finding.start_sample, finding.start_sample + finding.length_samples):
            continue
        engine.append(finding)
    kept = [finding for finding in findings if finding.kind != "pause"]
    kept.extend(engine)
    kept.extend(item for item in quiet if not any(_within(item, finding) for finding in engine))
    kept.sort(key=lambda item: item.start_sample)
    return kept


def explained_by_pauses(
    windows: tuple[QualityWindow, ...], findings: list[Finding]
) -> tuple[QualityWindow, ...]:
    """Las ventanas de ruido que son, en realidad, una asistolia confirmada.

    Una ventana de 10 s sin QRS sale `bad`/pSQI (o kSQI, basSQI, `no_beats`), y
    `pipeline._quality_findings` la pintaba como `noise_burst` —"los electrodos
    estaban bien y aun así no se pudo leer"— encima de la pausa CRITICAL que la
    explica. Son las que tienen al menos la mitad adentro de una pausa de hueco
    quieto. Solo se dejan de **pintar**: la ventana, la máscara de analizable,
    los intervalos de calidad y los totales no cambian.
    """
    spans = [
        (finding.start_sample, finding.start_sample + finding.length_samples)
        for finding in findings
        if finding.kind == "pause" and finding.metadata.get("quietGap")
    ]
    if not spans:
        return ()
    explained: list[QualityWindow] = []
    for window in windows:
        if window.level is not SignalQualityLevel.BAD or window.reason not in _NOISE_REASONS:
            continue
        low, high = window.start_sample, window.start_sample + window.length_samples
        inside = sum(max(0, min(high, end) - max(low, start)) for start, end in spans)
        if 2 * inside >= window.length_samples:
            explained.append(window)
    return tuple(explained)
