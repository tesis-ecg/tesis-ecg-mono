"""Etapa 1 — el gate de calidad. Decide qué tramo del registro es analizable.

Es lo primero que corre y lo que más impacto tiene: un artefacto de movimiento
se parece muchísimo más a una arritmia que a un latido normal, así que un motor
que no separa ruido primero produce cientos de falsos positivos por día y el
médico deja de mirar la herramienta.

Tres capas, y **ninguna manda sola**:

- **Capa A — el hardware.** Bits de `LEAD_OFF` y `ADC_SATURATED` que el AFE
  reporta por muestra. Determinística y sin discusión: si el electrodo está
  despegado no hay señal, no importa qué diga ningún índice espectral.
- **Capa B — índices de señal.** pSQI, kSQI y basSQI calculados acá con scipy,
  sobre la señal cruda sin la interferencia de la red (`deinterfere`).
- **Capa B' — el acuerdo entre detectores (bSQI).** Ver `rpeak_detection.py`.

La regla de combinación es **conservadora por diseño**: la ventana es `good`
solo si ninguna capa objeta. Alcanza con que una la degrade.

## Por qué los SQIs se calculan acá y no con `nk.ecg_quality`

Se midió, y hay tres razones concretas:

1. `method="zhao2018"` devuelve **un string para toda la señal que recibe**, no
   una serie. Para tener un valor por ventana de 10 s habría que llamarlo 360
   veces por lote, y cada llamada vuelve a correr `ecg_peaks` internamente.
2. Su implementación **descartó el índice qSQI** del paper original y
   redistribuyó los pesos. No es el ensemble que la cita promete.
3. Falla en las dos direcciones: le da `Barely acceptable` a un flatline —el
   modo de falla más obvio de un electrodo seco— y `Excellent` a ruido gaussiano
   puro sin un solo QRS.

Son tres fórmulas cerradas de treinta líneas de scipy, testeables con señales
sintéticas. Calcularlas acá deja la superficie de neurokit2 en exactamente dos
funciones (`ecg_clean` y `ecg_peaks`), que es lo que hace que la dependencia sea
honesta y no decorativa.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from app.db.models.signal_quality import SignalQualityLevel
from app.ml.contracts import (
    Flags,
    Floats,
    Indices,
    Mask,
    QualityReason,
    QualityReport,
    QualityThresholds,
    QualityWindow,
    Signal,
)
from app.ml.decompression import (
    FLAG_ADC_SATURATED,
    FLAG_LEAD_OFF,
    FLAG_RLD_OFF,
    FLAG_SQI_MASK,
    FLAG_SQI_SHIFT,
    SQ_BAD,
)
from app.ml.rpeak_detection import beat_sqi

#: Fracción de la ventana que tiene que estar afectada para que un bit del
#: hardware la invalide. Un electrodo que rebota una muestra no arruina 10 s de
#: registro; uno despegado marca la ventana entera.
VETO_FRACTION = 0.05

#: Bandas de los índices espectrales (Zhao & Zhang, 2018).
QRS_BAND = (5.0, 15.0)
SIGNAL_BAND = (5.0, 40.0)
FULL_BAND = (0.0, 40.0)

#: Banda de deriva de línea de base: **0-0,5 Hz y no los 0-1 Hz del paper**.
#:
#: Medido: a 60 lpm el fundamental cardíaco cae justo en 1 Hz, así que con la
#: banda del paper el propio ritmo del paciente cuenta como "deriva" y un ECG
#: perfectamente limpio da basSQI = 0,907 — apenas por encima del umbral de 0,90,
#: y a 100 lpm la cosa empeora. Con 0-0,5 Hz el mismo ECG da 0,987 y uno con
#: deriva real de 0,15 Hz da 0,795: la separación pasa de nula a un factor claro.
BASELINE_BAND = (0.0, 0.5)

#: Factor de calidad de los notch de red, **el mismo en todas las armónicas**.
#:
#: Medido en las capturas del chaleco (Welch con resolución de 0,015 Hz): la red
#: no aparece en 50,00 Hz sino entre 50,14 y 50,34 Hz según la captura, y las
#: armónicas en múltiplos de eso. El corrimiento escala con la armónica —es el
#: reloj de muestreo del chaleco, no la red—, y con Q constante el ancho de banda
#: (f0/Q) escala igual, así que la atenuación es la misma en las cuatro.
#:
#: El Q sale de la respuesta en frecuencia, no de las capturas. Ida y vuelta de
#: `filtfilt`, en el corrimiento medido:
#:
#: | Q  | 50,15 Hz | 50,25 Hz | 50,30 Hz | 50,40 Hz | 40 Hz (borde de banda) |
#: |----|----------|----------|----------|----------|------------------------|
#: | 15 |   42 dB  |   33 dB  |   30 dB  |   25 dB  | −0,19 dB               |
#: | 30 |   30 dB  |   22 dB  |   19 dB  |   15 dB  | −0,05 dB               |
#:
#: Con 30 el margen es nulo: a 50,25 Hz y 10 mV pico a pico (chaleco enchufado)
#: el residuo de red ya vuelve a tumbar la curtosis. Con 15 sobra y el costo es
#: chico: 0,19 dB en el borde de la banda de los índices, nada en la del QRS, y
#: sobre MIT-BIH la mediana del kSQI de un ECG limpio baja 0,011. Además el
#: transitorio dura la mitad (constante de tiempo Q/(π·f0) ≈ 0,1 s).
#:
#: Cruzado contra las 16 capturas de canal 2 (`tools/vest/evaluate.py`), sin
#: elegir el valor ahí: de Q = 15 a Q = 30 las ventanas son idénticas, Q = 45 ya
#: pierde buenas y Q = 10 abre dos que con 15-30 no pasan.
MAINS_NOTCH_Q = 15.0

#: Constantes de tiempo del notch que se descartan **para la curtosis** en cada
#: borde de un tramo filtrado (≈ 1 s a 50 Hz con Q = 15).
#:
#: `filtfilt` es de fase cero, así que su transitorio sale para los dos lados de
#: cualquier discontinuidad: el arranque del lote y, sobre todo, el riel de un
#: electrodo despegado. Medido con un escalón de 806 mV (riel a riel): a 0,1 s
#: quedan 10,6 mV oscilando a 50 Hz, a 0,48 s 0,17 mV, a 1 s 0,001 mV. Una ráfaga
#: de milivoltios es exactamente lo que la curtosis —cuarto momento— premia: con
#: el notch corriendo por encima del riel, ruido sin un solo QRS al lado de un
#: `lead_off` pasaba de kSQI 3 a 190 y el gate lo daba por bueno.
MAINS_SETTLE_TAUS = 10.0

#: Fracción mínima de la ventana que tiene que quedar asentada (lejos de bordes
#: y de muestras inválidas) para medir la curtosis sobre la señal sin red. Por
#: debajo se mide sobre las muestras válidas de la ventana cruda, como si la red
#: no se quitara: más conservador, nunca más permisivo.
MIN_SETTLED_FRACTION = 0.5

#: Por debajo de esto no es una red eléctrica (el setting `ml_mains_hz` acepta
#: 0 o 45-65 Hz). Con 0,5 Hz serían 499 notch que se comen el fundamental
#: cardíaco, y con 1e-6 el bucle de armónicas no termina.
MIN_MAINS_HZ = 45.0


def sample_runs(mask: Mask) -> list[tuple[int, int]]:
    """Tramos `(inicio, largo)` donde `mask` es verdadera."""
    if mask.size == 0 or not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(s), int(e - s)) for s, e in zip(edges[0::2], edges[1::2], strict=True)]


def window_bounds(n_samples: int, window_samples: int) -> list[tuple[int, int]]:
    """Ventanas `(inicio, largo)` que cubren la señal **entera**, sin huecos.

    La última absorbe el remanente en vez de descartarlo. Que quede más larga que
    las otras es preferible a dejar hasta 10 s del registro sin evaluar: un tramo
    sin nivel de calidad no se distingue de uno bueno cuando el visor lo dibuja.
    """
    if n_samples <= 0:
        return []
    if n_samples <= window_samples:
        return [(0, n_samples)]
    count = n_samples // window_samples
    bounds = [(index * window_samples, window_samples) for index in range(count - 1)]
    last_start = (count - 1) * window_samples
    bounds.append((last_start, n_samples - last_start))
    return bounds


def block_window_bounds(
    n_samples: int, window_samples: int, context_samples: int = 0, lookahead_samples: int = 0
) -> list[tuple[int, int]]:
    """`window_bounds` en tres tramos: contexto izquierdo, parte nueva y contexto derecho.

    Cada tramo se cubre desde su propio inicio, así que siempre hay un borde de
    ventana **exactamente** en `context_samples` y otro en el final de la parte
    nueva (`n_samples - lookahead_samples`), sean o no múltiplos del largo de
    ventana. Es lo que permite reportar solo las ventanas de la parte nueva sin
    que ninguna mezcle muestras de dos lados: una ventana a caballo del borde la
    evaluarían los dos bloques que lo comparten, contada dos veces en los
    totales del estudio. La última ventana de cada tramo absorbe su remanente,
    igual que en `window_bounds`.
    """
    total = max(n_samples, 0)
    context = min(max(context_samples, 0), total)
    end = max(total - max(lookahead_samples, 0), context)
    bounds = window_bounds(context, window_samples)
    for offset, size in ((context, end - context), (end, total - end)):
        bounds.extend(
            (start + offset, length) for start, length in window_bounds(size, window_samples)
        )
    return bounds


#: Margen que se saca de la máscara de analizable a cada lado de un empalme
#: (`exclude_splices`). Alcanza con una muestra para que el R-R que lo cruza
#: quede inválido; el resto cubre el corrimiento de un R que el detector ubicó
#: sobre el salto mismo.
SPLICE_GUARD_MS = 200

#: Cuánto puede estar el empalme **después** del índice declarado. Un
#: `internal_gap` se ancla al inicio de su trama porque la cabecera solo dice
#: que falta señal adentro, no dónde (`FrameInfo.gap_beyond_clock_ms`). Una
#: trama de ECG real trae 0,3-0,4 s (140-180 muestras a 500 Hz); una más larga
#: solo sale de señal casi plana, que el gate ya rechaza por su cuenta.
SPLICE_SPAN_MS = 1000


def exclude_splices(analyzable: Mask, splices: Sequence[tuple[int, int]], sample_rate: int) -> Mask:
    """Saca de la máscara de analizable el entorno de cada empalme del buffer.

    Un `frame_gap` o un `internal_gap` es adquisición perdida **sin muestras
    que la representen**: el buffer del estudio es continuo y las muestras de
    los dos lados del hueco quedan pegadas. Un R-R que cruza el empalme no mide
    nada —le faltan los latidos del hueco— y un latido cuya ventana lo cruza
    mezcla dos instantes distintos. Con el entorno fuera de la máscara,
    `build_rr` invalida ese intervalo y `extract_beats` descarta ese latido, sin
    que ninguno de los dos tenga que saber de huecos.

    `splices` son pares `(inicio, largo)` relativos a la señal, tal como los
    guarda la Capa A en `startSampleIndex`/`sampleCount`. **El largo no se usa
    para ubicar nada**: es tiempo perdido, no muestras del buffer. Los que caen
    enteros fuera de la señal se ignoran; los demás se recortan a sus bordes.
    Las ventanas de calidad no cambian: el empalme no dice nada de la señal de
    cada lado.
    """
    if not splices:
        return analyzable
    n_samples = int(analyzable.size)
    guard = int(np.ceil(SPLICE_GUARD_MS * sample_rate / 1000))
    span = int(np.ceil(SPLICE_SPAN_MS * sample_rate / 1000))
    masked = analyzable.copy()
    for start, _length in splices:
        if start >= n_samples or start + span <= 0:
            continue
        low = max(start - guard, 0)
        high = min(start + span + guard, n_samples)
        masked[low:high] = False
    return masked


# --------------------------------------------------------------------------- #
# Capa A — bits del hardware
# --------------------------------------------------------------------------- #


def hardware_veto(window_flags: Flags) -> QualityReason | None:
    """Qué bit del AFE invalida esta ventana, si alguno.

    `RLD_OFF` **no** invalida: degrada el rechazo de modo común, pero el par
    RA-LL sigue midiendo una diferencia de potencial real (`INTEGRACION.md` §4.5,
    regla 2). Sí exime a la saturación, porque sin pierna derecha la señal se va
    al riel por deriva de modo común y no por un artefacto del paciente.
    """
    if window_flags.size == 0:
        return None
    threshold = max(1, int(window_flags.size * VETO_FRACTION))
    if int(np.count_nonzero(window_flags & FLAG_LEAD_OFF)) >= threshold:
        return "lead_off"
    saturated = (window_flags & FLAG_ADC_SATURATED) != 0
    rld_ok = (window_flags & FLAG_RLD_OFF) == 0
    if int(np.count_nonzero(saturated & rld_ok)) >= threshold:
        return "saturated"
    # El SQI que ya calculó el firmware. No reemplaza a la Capa B —es un umbral
    # de amplitud sobre el MCU— pero cuando dice "inutilizable" durante media
    # ventana, coincide.
    unanalyzable = ((window_flags & FLAG_SQI_MASK) >> FLAG_SQI_SHIFT) == SQ_BAD
    if int(np.count_nonzero(unanalyzable)) >= window_flags.size // 2:
        return "firmware_sqi"
    return None


def is_flatline(window: Signal, flatline_mv: float) -> bool:
    """Amplitud pico a pico robusta por debajo del umbral.

    Percentiles 5-95 y no min-max: un solo spike de conmutación levantaría el
    rango de una línea perfectamente plana y la haría pasar por señal.
    """
    finite = window[np.isfinite(window)]
    if finite.size < 2:
        return True
    low, high = np.percentile(finite.astype(np.float64), [5.0, 95.0])
    return bool(high - low < flatline_mv)


def has_gaps(window: Signal) -> bool:
    """Muestras no finitas por encima de la misma fracción que veta la Capa A.

    El lote real no las trae —`decode_batch` arma la señal con enteros—, pero
    una señal importada sí. Antes de quitar la red un NaN tumbaba los índices a
    cero; después de quitarla vale 0 (`remove_mains`), y tres segundos planos en
    cero **inflan** la curtosis. Un hueco no es señal: se trata como línea plana.
    """
    threshold = max(1, int(window.size * VETO_FRACTION))
    return int(np.count_nonzero(~np.isfinite(window))) >= threshold


# --------------------------------------------------------------------------- #
# Capa B — índices espectrales
# --------------------------------------------------------------------------- #


def remove_mains(signal: Signal, sample_rate: int, mains_hz: float) -> Signal:
    """Saca la media y la red (fundamental + armónicas) antes de los índices.

    Medido sobre el canal de ECG del chaleco: con ~2 mV pico a pico de 50 Hz
    encima, un ECG perfectamente visible da kSQI ≈ 2 —la sinusoide domina la
    distribución de amplitudes y la curtosis tiende a la de una senoide, 1,5— y
    el gate rechaza 29 de 29 ventanas. La red no es ruido del paciente: es una
    línea espectral angosta que se puede quitar sin tocar nada más.

    **No es `ecg_clean`.** Solo saca líneas de ~2 Hz en múltiplos de la red; el
    ruido de banda ancha queda entero. Un filtro lineal sobre ruido gaussiano
    sigue dando ruido gaussiano, así que la curtosis de 3 que lo delata (ver
    `spectral_sqi`) no se mueve. El pasa-banda de `ecg_clean`, en cambio, sí la
    rompe.

    - Notch de `scipy.signal.iirnotch` en `mains_hz` y en cada armónica
      **estrictamente** por debajo de Nyquist, con `filtfilt`: fase cero, el
      QRS no se corre ni se deforma.
    - `mains_hz <= 0` apaga los notch y devuelve **solo la copia sin media**. Ni
      Welch (que ya resta la media de cada segmento) ni la curtosis cambian con
      un corrimiento constante, así que apagado los índices dan lo mismo que
      sobre la señal cruda, salvo redondeo de float32.
    - NaN e infinitos van a cero **después** de restar la media (calculada sobre
      las muestras finitas): la convención de `clean_signal`, pero sin el escalón
      de una línea de base de 50 mV contra un cero absoluto.
    - Más corta que el `padlen` de `filtfilt` no se puede filtrar: se devuelve
      sin media, sin excepción. Una ventana así ya da índices nulos.
    - `0 < mains_hz < MIN_MAINS_HZ` es un error de configuración y levanta
      `ValueError` (el setting ya lo impide): no es una red, y quitarla destruye
      el ECG o no termina.

    Filtra la señal **de corrido**: no sabe de lead-off ni de bordes. El que
    puentea el riel y descarta los transitorios es `deinterfere`.

    Siempre devuelve una copia float32, nunca la entrada.
    """
    from scipy import signal as sp_signal

    if 0 < mains_hz < MIN_MAINS_HZ:
        raise ValueError(f"mains_hz={mains_hz} no es una red eléctrica (0 o ≥ {MIN_MAINS_HZ})")
    data = signal.astype(np.float64)
    finite = np.isfinite(data)
    if not finite.any():
        return np.zeros(data.size, dtype=np.float32)
    data = np.where(finite, data - float(np.mean(data[finite])), 0.0)
    if mains_hz <= 0:
        return data.astype(np.float32)

    nyquist = sample_rate / 2.0
    order = 1
    while order * mains_hz < nyquist:
        numerator, denominator = sp_signal.iirnotch(
            order * mains_hz, MAINS_NOTCH_Q, fs=float(sample_rate)
        )
        # El mismo default de `filtfilt`, explícito para poder chequearlo antes.
        padlen = 3 * max(len(numerator), len(denominator))
        if data.size <= padlen:
            break
        data = sp_signal.filtfilt(numerator, denominator, data, padlen=padlen)
        order += 1
    return np.asarray(data, dtype=np.float32)


def invalid_samples(signal: Signal, flags: Flags) -> Mask:
    """Muestras que no son señal: no finitas, o en el riel según el AFE.

    `LEAD_OFF` y `ADC_SATURATED` sin mirar `RLD_OFF`: la saturación sin pierna
    derecha no veta la ventana (`hardware_veto`), pero la muestra sigue estando
    en el riel y no dice nada de la forma del ECG.
    """
    invalid = ~np.isfinite(signal)
    if flags.size == signal.size:
        invalid |= (flags & (FLAG_LEAD_OFF | FLAG_ADC_SATURATED)) != 0
    return invalid


def mains_settle_samples(sample_rate: int, mains_hz: float) -> int:
    """Muestras que el notch tarda en asentarse después de un borde. Cero si está apagado.

    La constante de tiempo del polo es Q/(π·f0); la de la fundamental es la más
    larga porque con Q constante las armónicas son más anchas y se apagan antes.
    """
    if mains_hz <= 0:
        return 0
    seconds = MAINS_SETTLE_TAUS * MAINS_NOTCH_Q / (np.pi * mains_hz)
    return int(np.ceil(seconds * sample_rate))


def deinterfere(
    signal: Signal, flags: Flags, sample_rate: int, mains_hz: float
) -> tuple[Signal, Mask]:
    """La señal sin red para los índices, y dónde es confiable para la curtosis.

    El notch **nunca filtra el riel**. Las muestras inválidas (`LEAD_OFF`,
    `ADC_SATURATED`, no finitas) se puentean con una recta entre sus vecinas
    válidas antes de filtrar, y después se les devuelve su valor crudo. Filtrar
    por encima de un escalón de cientos de milivoltios deja milivoltios de
    oscilación a los dos lados (`MAINS_SETTLE_TAUS`); contra el puente, el
    borde solo ve la red que se corta, del orden de lo que deja el arranque del
    lote. Una sola pasada sobre la señal entera: partirla en tramos costaba una
    llamada a `filtfilt` por tramo, y un electrodo que rebota cada 40 ms son
    90.000 tramos (26 s de CPU) por lote de una hora.

    - Las muestras inválidas quedan crudas sin media y las válidas conservan su
      nivel: el escalón contra el riel que ven pSQI y basSQI es el mismo que sin
      quitar la red. Esos dos índices miden bandas por debajo de 40 Hz y el
      transitorio vive en 50 Hz y arriba: no lo ven.
    - `settled` son las muestras a más de `mains_settle_samples` de cualquier
      inválida y de los bordes del lote. Es lo que mira la curtosis, que sí es
      sensible a una ráfaga corta.

    Con `mains_hz <= 0` no hay notch, ni transitorio, ni margen: la señal es la
    cruda sin media y `settled` son las muestras válidas.
    """
    centered = remove_mains(signal, sample_rate, 0.0)
    invalid = invalid_samples(signal, flags)
    if mains_hz <= 0:
        return centered, ~invalid
    n_samples = int(signal.size)
    valid_positions = np.flatnonzero(~invalid)
    if valid_positions.size == 0:
        return centered, np.zeros(n_samples, dtype=bool)

    bridged = centered.copy()
    invalid_positions = np.flatnonzero(invalid)
    if invalid_positions.size:
        bridged[invalid_positions] = np.interp(
            invalid_positions, valid_positions, bridged[valid_positions]
        )
    # `remove_mains` resta la media del puente; se la devuelve para que las
    # válidas queden al mismo nivel que en `centered` y el escalón no se mueva.
    bridge_mean = np.float32(np.mean(bridged, dtype=np.float64))
    output = remove_mains(bridged, sample_rate, mains_hz) + bridge_mean
    output[invalid_positions] = centered[invalid_positions]

    margin = mains_settle_samples(sample_rate, mains_hz)
    positions = np.arange(n_samples)
    invalid_before = np.concatenate(([0], np.cumsum(invalid, dtype=np.int64)))
    low = np.clip(positions - margin, 0, n_samples)
    high = np.clip(positions + margin + 1, 0, n_samples)
    settled = (
        (invalid_before[high] - invalid_before[low] == 0)
        & (positions >= margin)
        & (positions < n_samples - margin)
    )
    return output, settled


def _band_power(freqs: Floats, psd: Floats, band: tuple[float, float]) -> float:
    selected = (freqs >= band[0]) & (freqs <= band[1])
    if not selected.any():
        return 0.0
    return float(np.trapezoid(psd[selected], freqs[selected]))


def spectral_sqi(
    window: Signal, sample_rate: int, *, kurtosis_samples: Signal | None = None
) -> tuple[float, float, float]:
    """`(pSQI, kSQI, basSQI)` de una ventana.

    **Sobre la señal cruda, nunca sobre la limpia.** No es un detalle: se midió
    ruido gaussiano puro, sin un solo QRS, por los dos caminos.

    | Señal                | pSQI  | kSQI  | basSQI |
    |----------------------|-------|-------|--------|
    | ruido puro, cruda    | 0,316 |  3,07 | 0,996  |
    | ruido puro, filtrada | 0,651 | 12,48 | 0,858  |
    | ECG limpio, cruda    | 0,677 | 15,42 | 0,987  |

    Filtrado, el ruido blanco **pasa los tres umbrales**: `ecg_clean` lo recorta a
    la banda del QRS y lo deja pareciéndose a un ECG. Es la explicación mecánica
    del falso-pase de `nk.ecg_quality(method="zhao2018")`, que trabaja sobre la
    señal filtrada. Sobre la cruda, la curtosis de 3,07 —la de una gaussiana— lo
    delata sin ambigüedad.

    Y el basSQI solo tiene sentido crudo por definición: mide cuánta energía se
    fue a la deriva de línea de base, y el pasa-altos ya la eliminó (medido: 0,879
    sin deriva contra 0,880 con deriva fuerte, o sea que no discrimina nada).

    "Cruda" quiere decir sin pasa-banda: `assess_quality` le pasa la señal con la
    media y las líneas de red quitadas (`deinterfere`) y nada más. Eso no toca
    la deriva ni el ruido de banda ancha, que es lo que estos índices miden.

    `kurtosis_samples`, si viene, reemplaza a la ventana **solo para el kSQI**:
    la curtosis es un estadístico de la distribución de amplitudes, no le
    importa la contigüidad, y así puede dejar afuera los bordes donde el notch
    todavía no se asentó sin que Welch pierda la señal continua que necesita.
    """
    from scipy import signal as sp_signal
    from scipy import stats as sp_stats

    if window.size < sample_rate // 2:
        return 0.0, 0.0, 0.0
    data = window.astype(np.float64)
    nperseg = min(data.size, 1024)
    raw_freqs, raw_psd = sp_signal.welch(data, fs=float(sample_rate), nperseg=nperseg)
    freqs = np.asarray(raw_freqs, dtype=np.float32)
    psd = np.asarray(raw_psd, dtype=np.float32)

    signal_power = _band_power(freqs, psd, SIGNAL_BAND)
    psqi = _band_power(freqs, psd, QRS_BAND) / signal_power if signal_power > 0 else 0.0

    full_power = _band_power(freqs, psd, FULL_BAND)
    bassqi = 1.0 - _band_power(freqs, psd, BASELINE_BAND) / full_power if full_power > 0 else 0.0

    # Curtosis de Pearson (normal = 3). Un QRS es un pico raro y angosto sobre
    # una línea de base: la distribución de amplitudes queda muy leptocúrtica.
    # El ruido gaussiano da ~3 y por ahí se lo atrapa.
    kurtosis_data = data if kurtosis_samples is None else kurtosis_samples.astype(np.float64)
    ksqi = (
        float(np.asarray(sp_stats.kurtosis(kurtosis_data, fisher=False)))
        if kurtosis_data.size >= 4
        else 0.0
    )
    if not np.isfinite(ksqi):
        ksqi = 0.0
    return psqi, ksqi, bassqi


# --------------------------------------------------------------------------- #
# Combinación
# --------------------------------------------------------------------------- #


def assess_quality(
    signal: Signal,
    flags: Flags,
    cleaned: Signal,
    firmware_peaks: Indices,
    detected_peaks: Indices,
    *,
    sample_rate: int,
    thresholds: QualityThresholds,
    context_samples: int = 0,
    lookahead_samples: int = 0,
    flags_known: Mask | None = None,
) -> QualityReport:
    """Evalúa el lote ventana por ventana y devuelve la máscara de analizable.

    `analyzable` es verdadera solo donde el nivel es `good`: la Etapa 2 compara
    formas de onda, y una ventana `marginal` sirve para contar latidos pero no
    para afirmar que uno tiene una morfología distinta.

    La red se quita **una vez por llamada**, sobre la señal entera con el riel
    puenteado y no ventana por ventana (`deinterfere`): filtrar cada ventana sola
    pondría un transitorio en cada borde de 10 s. Los transitorios que quedan
    —arranque del lote, bordes contra el riel— no entran en la curtosis. Solo los
    índices espectrales ven la señal sin red; la línea plana y la Capa A miran la
    cruda: preguntan si el AFE entrega señal, no si esa señal es un ECG, y la
    respuesta no cambia porque sea red.

    `context_samples` y `lookahead_samples` parten las ventanas en tres tramos
    (`block_window_bounds`): el reporte trae las de todos, y es el pipeline el
    que decide cuáles informa. La red sí se quita de corrido sobre todo el
    bloque, los dos contextos incluidos: así ni el arranque ni el final de la
    parte nueva son un borde de filtrado, y la curtosis no les descarta el
    margen de asentamiento de `deinterfere`, que queda solo en los extremos de
    lo leído —ahí sí hay un borde—. Sin contexto derecho, la última ventana de
    cada bloque se juzgaba contra ese borde y nadie la volvía a evaluar: en
    MIT-BIH, 5 de 180 cambiaban de nivel respecto del análisis de corrido.

    El bSQI se pide en una ventana si el firmware marca R en el bloque **y**
    los flags de esa ventana están archivados (`flags_known`, `None` = todos).
    Una ventana de un segmento viejo trae flags en cero: sin la máscara, un
    bloque que mezclaba segmentos con y sin flags comparaba el ECG limpio de
    la parte sin flags contra un firmware "que no vio ningún R" y lo dejaba
    MARGINAL/bsqi, fuera del ritmo, la morfología y los totales.
    """
    n_samples = int(signal.size)
    analyzable = np.zeros(n_samples, dtype=bool)
    firmware_available = firmware_peaks.size > 0
    windows: list[QualityWindow] = []
    deinterfered, settled = deinterfere(signal, flags, sample_rate, thresholds.mains_hz)

    for start, length in block_window_bounds(
        n_samples, thresholds.window_samples, context_samples, lookahead_samples
    ):
        end = start + length
        # La parte nueva más corta que una ventana —la cola de una corrida que
        # se cerró a 0,4 s de un múltiplo del bloque— se **evalúa** sobre la
        # última ventana entera, prestándose señal del contexto, y se informa
        # con sus propios bordes. Evaluada sola, 0,4 s de ECG limpio no llegan
        # a mostrar la potencia del QRS y salían BAD/psqi con una banda de ruido
        # inventada; de una sola vez, ese remanente lo absorbía la ventana
        # anterior (`window_bounds`). Lo mismo un contexto derecho corto, que
        # se presta señal de la parte nueva.
        low = (
            max(end - thresholds.window_samples, 0)
            if 0 < start and start >= context_samples and length < thresholds.window_samples
            else start
        )
        level, reason, metrics = _assess_window(
            signal[low:end],
            deinterfered[low:end],
            settled[low:end],
            flags[low:end],
            cleaned[low:end] if cleaned.size == n_samples else signal[low:end],
            firmware_peaks[(firmware_peaks >= low) & (firmware_peaks < end)] - low,
            detected_peaks[(detected_peaks >= low) & (detected_peaks < end)] - low,
            sample_rate=sample_rate,
            thresholds=thresholds,
            firmware_available=firmware_available
            and (flags_known is None or bool(flags_known[low:end].all())),
        )
        windows.append(
            QualityWindow(
                start_sample=start,
                length_samples=length,
                level=level,
                reason=reason,
                psqi=metrics[0],
                ksqi=metrics[1],
                bassqi=metrics[2],
                bsqi=metrics[3],
            )
        )
        if level is SignalQualityLevel.GOOD:
            analyzable[start:end] = True

    return QualityReport(
        windows=tuple(windows),
        analyzable=analyzable,
        firmware_peaks_available=firmware_available,
    )


def _assess_window(
    window: Signal,
    window_deinterfered: Signal,
    window_settled: Mask,
    window_flags: Flags,
    window_cleaned: Signal,
    firmware_peaks: Indices,
    detected_peaks: Indices,
    *,
    sample_rate: int,
    thresholds: QualityThresholds,
    firmware_available: bool,
) -> tuple[SignalQualityLevel, QualityReason, tuple[float | None, ...]]:
    veto = hardware_veto(window_flags)
    if veto is not None:
        # Capa A gana sin calcular nada más: no tiene sentido pedirle un índice
        # espectral a un electrodo despegado.
        return SignalQualityLevel.BAD, veto, (None, None, None, None)

    if has_gaps(window) or is_flatline(window, thresholds.flatline_mv):
        return SignalQualityLevel.BAD, "flatline", (None, None, None, None)

    # La curtosis solo sobre muestras asentadas: lejos de los bordes de tramo y
    # sin las inválidas que la Capa A dejó pasar por ser menos del 5 % (un riel
    # de 400 mV domina el cuarto momento aunque sean diez muestras). Si queda
    # poco, sobre las válidas de la ventana cruda: la red le baja la curtosis,
    # no la sube, así que el respaldo es más estricto y nunca más permisivo.
    settled_count = int(np.count_nonzero(window_settled))
    kurtosis_samples = (
        window_deinterfered[window_settled]
        if settled_count >= MIN_SETTLED_FRACTION * window.size
        else window[~invalid_samples(window, window_flags)]
    )
    psqi, ksqi, bassqi = spectral_sqi(
        window_deinterfered, sample_rate, kurtosis_samples=kurtosis_samples
    )
    bsqi = (
        beat_sqi(firmware_peaks, detected_peaks, thresholds.bsqi_tolerance_samples)
        if firmware_available
        else None
    )
    metrics: tuple[float | None, ...] = (psqi, ksqi, bassqi, bsqi)

    # El primero que falla, en orden fijo (pSQI, kSQI, basSQI): una ventana que
    # falla dos índices tiene un solo motivo, y siempre el mismo.
    if psqi < thresholds.psqi_min:
        return SignalQualityLevel.BAD, "psqi", metrics
    if ksqi < thresholds.ksqi_min:
        return SignalQualityLevel.BAD, "ksqi", metrics
    if bassqi < thresholds.bassqi_min:
        return SignalQualityLevel.BAD, "bassqi", metrics

    # Ningún detector encontró un latido en 10 s de señal que pasó los índices
    # espectrales. Es el falso-pase que se midió sobre ruido gaussiano puro.
    if detected_peaks.size == 0 and firmware_peaks.size == 0:
        return SignalQualityLevel.BAD, "no_beats", metrics

    if bsqi is not None and bsqi < thresholds.bsqi_min:
        # Los dos detectores ven latidos pero no los mismos. Hay señal —se puede
        # contar y medir R-R— pero comparar morfologías sobre esto produciría
        # anomalías que son artefactos de alineación.
        return SignalQualityLevel.MARGINAL, "bsqi", metrics

    return SignalQualityLevel.GOOD, "ok", metrics


def _median_or_none(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(float(np.median(present)), 6)


def merge_windows(windows: Sequence[QualityWindow]) -> list[QualityWindow]:
    """Colapsa ventanas contiguas con el mismo `(nivel, motivo)` en intervalos.

    Un registro limpio de una hora pasa de 360 filas a una sola. El caso
    patológico —alternancia perfecta— no colapsa nada y deja 360, que sigue
    siendo un techo conocido y acotado.
    """
    merged: list[QualityWindow] = []
    buffer: list[QualityWindow] = []

    def flush() -> None:
        if not buffer:
            return
        first = buffer[0]
        last = buffer[-1]
        merged.append(
            QualityWindow(
                start_sample=first.start_sample,
                length_samples=last.start_sample + last.length_samples - first.start_sample,
                level=first.level,
                reason=first.reason,
                psqi=_median_or_none([item.psqi for item in buffer]),
                ksqi=_median_or_none([item.ksqi for item in buffer]),
                bassqi=_median_or_none([item.bassqi for item in buffer]),
                bsqi=_median_or_none([item.bsqi for item in buffer]),
            )
        )
        buffer.clear()

    for window in windows:
        if buffer and (buffer[-1].level is not window.level or buffer[-1].reason != window.reason):
            flush()
        buffer.append(window)
    flush()
    return merged


def window_counts(windows: Sequence[QualityWindow], merged: QualityWindow) -> int:
    """Cuántas ventanas originales entraron en un intervalo fusionado."""
    end = merged.start_sample + merged.length_samples
    return sum(1 for item in windows if merged.start_sample <= item.start_sample < end)
