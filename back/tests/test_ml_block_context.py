"""El motor sobre bloques con contexto izquierdo, y los totales que se suman.

Los lotes reales son de ~15 s: analizados de a uno, una taquicardia (30 s como
mínimo) no se podría detectar nunca y cada borde cortaría un R-R. El cursor de
bloques le pasa al motor ~5 min nuevos precedidos por hasta 1 min ya analizado.
Estos tests fijan las reglas que hacen eso seguro: del contexto se usa todo,
pero no se informa nada que ya haya informado el bloque anterior, y los totales
de bloques consecutivos suman exactamente lo mismo que un análisis entero.
"""

from dataclasses import replace

import numpy as np
import pytest

from app.core.config import settings
from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityLevel
from app.ml import pipeline
from app.ml.contracts import Finding, QualityWindow
from app.ml.episodes import apply_refractory
from app.ml.hrv import RRSeries, build_rr, hrv_summary
from app.ml.morphology import TemplateBank
from app.ml.quality import (
    SPLICE_GUARD_MS,
    SPLICE_SPAN_MS,
    assess_quality,
    block_window_bounds,
    exclude_splices,
    window_bounds,
)
from app.ml.rpeak_detection import (
    clean_signal,
    compensate_firmware_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)
from app.ml.totals import block_totals, combine_totals, summary_from_totals
from tests.ecg_synth import SAMPLE_RATE, synth_ecg

SR = SAMPLE_RATE
WINDOW = 10 * SR


def _config() -> pipeline.PipelineConfig:
    return pipeline.build_config(settings, SR)


def _analyze(
    signal_mv: np.ndarray,
    flags: np.ndarray,
    *,
    start: int = 0,
    context: int = 0,
    lookahead: int = 0,
    bank: TemplateBank | None = None,
    fold_key: str = "b1",
    gaps: tuple[tuple[int, int], ...] = (),
    flags_known: np.ndarray | None = None,
) -> pipeline.PipelineResult:
    config = _config()
    return pipeline.analyze_batch(
        signal_mv,
        flags,
        start_sample_index=start,
        bank=bank if bank is not None else pipeline.empty_bank(config),
        config=config,
        fold_key=fold_key,
        context_samples=context,
        lookahead_samples=lookahead,
        gap_samples=gaps,
        flags_known=flags_known,
    )


def _with_pause(start_s: float, length_s: float, duration_s: float = 120.0):
    """ECG con los latidos de `[start_s, start_s + length_s)` borrados.

    El tramo queda en el valor de la primera muestra y sin `FLAG_R_PEAK`, como
    en `test_una_pausa_real_se_detecta_y_avisa_al_paciente`.
    """
    ecg = synth_ecg(duration_s=duration_s)
    signal_mv = ecg.signal_mv.copy()
    flags = ecg.flags.copy()
    tramo = slice(int(start_s * SR), int((start_s + length_s) * SR))
    signal_mv[tramo] = ecg.signal_mv[0]
    flags[tramo] = 0
    return signal_mv, flags


def _pauses(result: pipeline.PipelineResult) -> list[Finding]:
    return [finding for finding in result.findings if finding.kind == "pause"]


# --------------------------------------------------------------------------- #
# Ritmo: qué se informa y qué no
# --------------------------------------------------------------------------- #


def test_un_hallazgo_entero_en_el_contexto_no_se_vuelve_a_informar() -> None:
    """La pausa de los 30 s ya la informó el bloque anterior: repetirla sería un
    segundo evento —y un segundo push al paciente— por la misma pausa."""
    signal_mv, flags = _with_pause(30.2, 2.6)
    sin_contexto = _analyze(signal_mv, flags)
    assert _pauses(sin_contexto), "la pausa tiene que verse sin contexto"

    con_contexto = _analyze(signal_mv, flags, start=1_000_000, context=60 * SR)
    assert _pauses(con_contexto) == []


def test_un_hallazgo_que_cruza_el_borde_se_conserva_con_su_inicio_en_el_contexto() -> None:
    """La pausa empieza en el contexto y termina en la parte nueva. El bloque
    anterior no la pudo ver —su señal se cortaba antes del segundo R— así que la
    informa este, con el inicio donde de verdad empieza."""
    signal_mv, flags = _with_pause(59.2, 2.6)
    offset, context = 1_000_000, 60 * SR

    anterior = _analyze(signal_mv[:context], flags[:context], start=offset)
    assert _pauses(anterior) == []

    result = _analyze(signal_mv, flags, start=offset, context=context)
    [pausa] = _pauses(result)
    assert pausa.start_sample < offset + context
    assert pausa.start_sample + pausa.length_samples > offset + context
    assert pausa.dedupe_key == f"pause:{pausa.start_sample}"


def test_la_refractariedad_funde_a_traves_del_borde_como_en_un_lote_unico() -> None:
    """Dos pausas a 5 s de distancia son UN hallazgo cuando se analizan juntas.
    Si el borde cae entre las dos tienen que seguir siéndolo: la fundida empieza
    en el contexto y la persistencia la empalma con el evento de la primera."""
    ecg = synth_ecg(duration_s=120.0)
    signal_mv = ecg.signal_mv.copy()
    flags = ecg.flags.copy()
    for start_s in (55.2, 61.2):
        tramo = slice(int(start_s * SR), int((start_s + 2.6) * SR))
        signal_mv[tramo] = ecg.signal_mv[0]
        flags[tramo] = 0

    entero = _pauses(_analyze(signal_mv, flags))
    assert len(entero) == 1
    assert entero[0].beat_count == 4

    context = 60 * SR
    [fundida] = _pauses(_analyze(signal_mv, flags, context=context))
    assert fundida.start_sample == entero[0].start_sample < context
    assert fundida.length_samples == entero[0].length_samples
    assert fundida.metadata["pauseSeconds"] == entero[0].metadata["pauseSeconds"]


def test_una_pausa_que_cierra_pegada_al_borde_la_informa_el_bloque_siguiente() -> None:
    """El R que cierra la pausa cae 15 ms antes del borde. El detector no ve un R
    cuyo QRS quedó cortado por el final de la señal, así que el bloque anterior
    puede no haberla visto; y para este bloque la pausa termina en su contexto.
    Descartarla por eso la perdía de los dos lados: la franja de guarda la deja
    pasar, y si el anterior sí la había escrito, la persistencia la empalma."""
    signal_mv, flags = _with_pause(59.6, 2.6, duration_s=180.0)
    peaks = synth_ecg(duration_s=180.0).rpeaks
    cierra = int(peaks[peaks > int(62.2 * SR)][0])
    borde = cierra + int(0.015 * SR)
    contexto = 60 * SR
    desde = borde - contexto

    [pausa] = _pauses(_analyze(signal_mv[desde:], flags[desde:], start=desde, context=contexto))
    assert pausa.start_sample < borde
    assert pausa.start_sample + pausa.length_samples == cierra
    [entera] = _pauses(_analyze(signal_mv, flags))
    assert (pausa.start_sample, pausa.length_samples) == (
        entera.start_sample,
        entera.length_samples,
    )


# --------------------------------------------------------------------------- #
# Morfología: qué se pliega al banco
# --------------------------------------------------------------------------- #


def test_los_latidos_del_contexto_no_se_pliegan_al_banco() -> None:
    """El bloque anterior ya los sumó: contarlos de nuevo inflaría `beatsSeen` y
    con él la carga (`burdenPct`) de cada morfología."""
    # Uno de cada seis: ~40 ectópicos en la parte nueva, por encima de los 30 que
    # hacen recurrente a un cluster y le permiten producir episodios.
    ecg = synth_ecg(duration_s=300.0, ectopic_every=6)
    offset, context = 500_000, 60 * SR
    result = _analyze(ecg.signal_mv, ecg.flags, start=offset, context=context)

    # Un latido es nuevo si su ventana termina después del borde: el R puede
    # caer hasta 250 ms antes (`BEAT_POST_MS`), nunca más.
    post = SR // 4
    nuevos = int(np.count_nonzero(ecg.rpeaks + post > context))
    assert 0 < result.bank.beats_seen <= nuevos
    assert result.bank.beats_seen == result.metrics["analyzedBeats"]
    # Las plantillas siguen en coordenadas absolutas, y ninguna arranca en el
    # contexto.
    assert all(t.first_sample + post > offset + context for t in result.bank.templates)
    episodios = [f for f in result.findings if f.kind == "morphology_anomaly"]
    assert episodios
    assert all(f.start_sample >= offset + context - SR // 2 for f in episodios)


def test_el_latido_pegado_al_borde_lo_pliega_el_bloque_siguiente() -> None:
    """El R a menos de 250 ms del final de un bloque no tiene ventana completa
    ahí. Si el bloque siguiente lo tomara por contexto —el R cae antes del
    borde— no lo plegaría nadie: un latido perdido por borde, y si es el
    ectópico, un hallazgo perdido."""
    ecg = synth_ecg(duration_s=240.0, ectopic_every=7)
    # El borde cae 100 ms después del R de 119,5 s (los normales caen en k + 0,5 s).
    boundary, context = int(119.6 * SR), 60 * SR
    assert np.any((ecg.rpeaks < boundary) & (ecg.rpeaks + SR // 4 > boundary))
    entero = _analyze(ecg.signal_mv, ecg.flags)

    primero = _analyze(ecg.signal_mv[:boundary], ecg.flags[:boundary], fold_key="a")
    desde = boundary - context
    segundo = _analyze(
        ecg.signal_mv[desde:],
        ecg.flags[desde:],
        start=desde,
        context=context,
        bank=primero.bank,
        fold_key="b",
    )
    assert segundo.bank.beats_seen == entero.bank.beats_seen


def test_un_par_de_ectopicos_a_caballo_del_borde_es_un_solo_episodio() -> None:
    """Dos ectópicos seguidos, uno a cada lado del borde de pertenencia (R a 119,3
    y 119,9 s con el borde en 120 s). Sin morfología recurrente, un grupo de un
    latido no llega al mínimo de dos: agrupando solo los latidos nuevos, cada
    bloque veía uno y el par se perdía. El segundo bloque agrupa también los del
    contexto y lo informa entero, con el inicio en el contexto."""
    from tests.test_ml_blocks import _ecg, _latidos

    latidos = sorted(
        [t for t in _latidos([(240.0, 60.0)]) if not 119.0 < t < 121.0] + [119.3, 119.9]
    )
    ectopicos = frozenset(i for i, t in enumerate(latidos) if t in (119.3, 119.9))
    signal_mv, flags = _ecg(latidos, 240.0, ectopicos=ectopicos)
    borde, contexto = 120 * SR, 60 * SR

    def episodios(result: pipeline.PipelineResult) -> list[tuple[int, int, int | None]]:
        return [
            (f.start_sample, f.length_samples, f.beat_count)
            for f in result.findings
            if f.kind == "morphology_anomaly"
        ]

    [entero] = episodios(_analyze(signal_mv, flags))
    assert entero[2] == 2
    primero = _analyze(signal_mv[:borde], flags[:borde], fold_key="a")
    assert episodios(primero) == []
    desde = borde - contexto
    segundo = _analyze(
        signal_mv[desde:],
        flags[desde:],
        start=desde,
        context=contexto,
        bank=primero.bank,
        fold_key="b",
    )
    assert episodios(segundo) == [entero]


def test_la_misma_clave_de_plegado_dos_veces_solo_puntua() -> None:
    """Un bloque que se reintenta trae la misma `fold_key`: el segundo análisis
    puntúa contra el banco pero no vuelve a sumar sus latidos."""
    ecg = synth_ecg(duration_s=180.0, ectopic_every=9)
    context = 60 * SR
    primero = _analyze(ecg.signal_mv, ecg.flags, context=context, fold_key="run:0:30000")
    segundo = _analyze(
        ecg.signal_mv, ecg.flags, context=context, bank=primero.bank, fold_key="run:0:30000"
    )
    assert primero.bank.last_fold_key == "run:0:30000"
    assert segundo.bank.beats_seen == primero.bank.beats_seen
    assert segundo.bank.templates == primero.bank.templates
    assert {f.dedupe_key for f in segundo.findings} == {f.dedupe_key for f in primero.findings}

    otro = _analyze(
        ecg.signal_mv, ecg.flags, context=context, bank=primero.bank, fold_key="run:0:60000"
    )
    assert otro.bank.beats_seen == 2 * primero.bank.beats_seen


def test_un_estado_viejo_conserva_su_ultima_clave_plegada() -> None:
    """`lastFoldedBatchId` es el nombre de antes del cursor. Un estudio que ya
    venía grabando no puede perder la red de seguridad al actualizar."""
    from app.ml.morphology import bank_from_state, bank_to_state

    bank = replace(pipeline.empty_bank(_config()), last_fold_key="lote-viejo")
    state, blob = bank_to_state(bank)
    assert state["lastFoldKey"] == "lote-viejo"
    legacy = {key: value for key, value in state.items() if key != "lastFoldKey"}
    legacy["lastFoldedBatchId"] = "lote-viejo"
    assert bank_from_state(legacy, blob, model_version=bank.model_version).last_fold_key == (
        "lote-viejo"
    )


# --------------------------------------------------------------------------- #
# Calidad: qué ventanas se informan
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("context", [60 * SR, 37 * SR + 123, 3 * SR])
def test_block_window_bounds_pone_un_borde_justo_en_el_contexto(context: int) -> None:
    n = 200 * SR + 77
    bounds = block_window_bounds(n, WINDOW, context)
    starts = [start for start, _ in bounds]
    assert context in starts
    # Cubre la señal entera, sin huecos ni solapes.
    assert starts[0] == 0
    assert all(a + la == b for (a, la), (b, _) in zip(bounds, bounds[1:], strict=False))
    assert bounds[-1][0] + bounds[-1][1] == n
    # Con contexto cero es exactamente `window_bounds`.
    assert block_window_bounds(n, WINDOW, 0) == window_bounds(n, WINDOW)


@pytest.mark.parametrize("context", [60 * SR, 37 * SR + 123])
def test_las_ventanas_informadas_son_solo_las_de_la_parte_nueva(context: int) -> None:
    ecg = synth_ecg(duration_s=180.0)
    offset = 2_000_000
    result = _analyze(ecg.signal_mv, ecg.flags, start=offset, context=context)

    nuevas = window_bounds(ecg.signal_mv.size - context, WINDOW)
    intervals = [interval for interval, _ in result.quality_intervals]
    assert intervals[0].start_sample == offset + context
    assert sum(interval.length_samples for interval in intervals) == ecg.signal_mv.size - context
    assert sum(count for _, count in result.quality_intervals) == len(nuevas)
    assert result.totals["windows"] == len(nuevas)
    assert result.totals["windowsGood"] == len(nuevas)
    assert result.totals["reason.ok"] == len(nuevas)
    assert result.totals["analyzedSamples"] == ecg.signal_mv.size - context


def test_un_contexto_ruidoso_no_produce_bandas_de_ruido_en_el_bloque() -> None:
    """El contexto ruidoso ya tiene su `noise_burst`, escrito por el bloque
    anterior. Acá solo sirve para invalidar los R-R que lo tocan."""
    ecg = synth_ecg(duration_s=180.0)
    ruidosa = ecg.signal_mv.copy()
    rng = np.random.default_rng(3)
    ruidosa[10 * SR : 50 * SR] += rng.normal(0.0, 0.6, 40 * SR).astype(np.float32)

    entero = _analyze(ruidosa, ecg.flags)
    assert any(f.kind == "noise_burst" for f in entero.findings)

    result = _analyze(ruidosa, ecg.flags, context=60 * SR)
    assert not any(f.kind == "noise_burst" for f in result.findings)
    assert {interval.level for interval, _ in result.quality_intervals} == {SignalQualityLevel.GOOD}


@pytest.mark.parametrize("cola_s", [0.4, 1.0])
def test_una_cola_mas_corta_que_una_ventana_se_evalua_con_senal_del_contexto(
    cola_s: float,
) -> None:
    """La cola de una corrida que cerró a menos de una ventana de un múltiplo del
    bloque. Evaluados solos, 0,4 s de ECG limpio no muestran la potencia del QRS
    y salían BAD/psqi con una banda de ruido inventada; de una sola vez, la
    ventana anterior absorbía ese resto. Se evalúa sobre la última ventana
    entera, prestada del contexto, y se informa con sus propios bordes."""
    contexto = 60 * SR
    ecg = synth_ecg(duration_s=60.0 + cola_s + 1.0)
    fin = contexto + int(cola_s * SR)
    result = _analyze(ecg.signal_mv[:fin], ecg.flags[:fin], context=contexto)

    [(intervalo, ventanas)] = result.quality_intervals
    assert (intervalo.start_sample, intervalo.length_samples) == (contexto, fin - contexto)
    assert intervalo.level is SignalQualityLevel.GOOD
    assert ventanas == 1
    assert not any(f.kind == "noise_burst" for f in result.findings)


def test_los_flags_que_faltan_no_le_piden_bsqi_a_su_ventana() -> None:
    """Un bloque que cruza el despliegue: los primeros 200 s vienen de segmentos
    sin flags archivados (llegan en cero) y el resto con los R del firmware.
    Decidiendo el bSQI por el bloque entero, el ECG limpio de la parte sin flags
    se comparaba contra un firmware "que no vio ningún R" y salía MARGINAL/bsqi,
    fuera del ritmo, la morfología y los totales."""
    ecg = synth_ecg(duration_s=360.0)
    flags = ecg.flags.copy()
    sin_flags = 200 * SR
    flags[:sin_flags] = 0
    conocidos = np.arange(flags.size) >= sin_flags

    result = _analyze(ecg.signal_mv, flags, context=60 * SR, flags_known=conocidos)

    assert {interval.level for interval, _ in result.quality_intervals} == {SignalQualityLevel.GOOD}
    assert result.totals["windowsGood"] == result.totals["windows"] == 30
    config = _config()
    cleaned = clean_signal(ecg.signal_mv, SR)
    ventanas = assess_quality(
        ecg.signal_mv,
        flags,
        cleaned,
        compensate_firmware_peaks(
            firmware_rpeaks(flags),
            lag_samples=config.quality.firmware_lag_samples,
            refractory_samples=config.quality.firmware_refractory_samples,
        ),
        detect_rpeaks(cleaned, SR),
        sample_rate=SR,
        thresholds=config.quality,
        flags_known=conocidos,
    ).windows
    # El bSQI se calcula donde hay flags y no donde faltan.
    assert all(w.bsqi is None for w in ventanas if w.start_sample + w.length_samples <= sin_flags)
    assert all(w.bsqi is not None for w in ventanas if w.start_sample >= sin_flags)


@pytest.mark.parametrize("lookahead", [30 * SR, 4 * SR + 321])
def test_block_window_bounds_pone_un_borde_en_cada_lado_de_la_parte_nueva(lookahead: int) -> None:
    n, context = 400 * SR, 60 * SR
    bounds = block_window_bounds(n, WINDOW, context, lookahead)
    starts = [start for start, _ in bounds]
    assert context in starts and n - lookahead in starts
    assert all(a + la == b for (a, la), (b, _) in zip(bounds, bounds[1:], strict=False))
    assert bounds[-1][0] + bounds[-1][1] == n
    # La parte nueva se cubre igual que sin contexto derecho.
    nueva = [(a, la) for a, la in bounds if context <= a < n - lookahead]
    assert nueva == [(a + context, la) for a, la in window_bounds(n - lookahead - context, WINDOW)]


def test_del_contexto_derecho_no_se_informa_nada() -> None:
    """Ni ventanas, ni latidos que contar o plegar, ni un hallazgo que empiece
    ahí: todo eso es del bloque siguiente. La pausa de los 125 s cae en el
    contexto derecho de un bloque que termina a los 120."""
    signal_mv, flags = _with_pause(125.2, 2.6, duration_s=160.0)
    fin, lookahead = 120 * SR, 40 * SR
    entero = _analyze(signal_mv, flags)
    assert _pauses(entero), "la pausa tiene que verse de una sola vez"
    sin_derecho = _analyze(signal_mv[:fin], flags[:fin])

    result = _analyze(signal_mv, flags, lookahead=lookahead)

    assert _pauses(result) == []
    intervals = [interval for interval, _ in result.quality_intervals]
    assert intervals[-1].start_sample + intervals[-1].length_samples == fin
    assert result.totals["analyzedSamples"] == fin
    assert result.totals["windows"] == 12
    assert result.totals["beats"] == sin_derecho.totals["beats"]
    assert result.bank.beats_seen == sin_derecho.bank.beats_seen


def test_un_hallazgo_que_empieza_en_la_parte_nueva_se_informa_entero() -> None:
    """La pausa abre en la parte nueva y cierra en el contexto derecho: sin él,
    este bloque no veía el R que la cierra. Se informa entera, y el bloque
    siguiente la vuelve a ver desde su contexto y la persistencia la empalma."""
    signal_mv, flags = _with_pause(119.2, 2.6, duration_s=160.0)
    result = _analyze(signal_mv, flags, lookahead=40 * SR)
    [pausa] = _pauses(result)
    [referencia] = _pauses(_analyze(signal_mv, flags))
    assert (pausa.start_sample, pausa.length_samples) == (
        referencia.start_sample,
        referencia.length_samples,
    )
    assert pausa.start_sample < 120 * SR < pausa.start_sample + pausa.length_samples


# --------------------------------------------------------------------------- #
# Totales: dos bloques suman lo mismo que uno
# --------------------------------------------------------------------------- #


#: Dónde queda el latido de referencia respecto del borde, en segundos
#: (positivo: después), y si la referencia es un ectópico
#: (`_bloques_contra_entero`).
_BORDES = {
    # El borde corta el QRS antes del R: sin contexto derecho, el detector
    # ponía un R fantasma sobre la subida cortada y el bloque lo contaba (+1
    # latido, un NN acortado, un RMSSD inflado).
    "qrs_cortado": (0.074, False),
    "qrs_cortado_temprano": (0.120, False),
    # El R en los últimos milisegundos del bloque: no lo veía ninguno (-1).
    "r_pegado_al_borde": (-0.010, False),
    "r_a_30_ms_del_borde": (-0.030, False),
    # El bloque termina en un ectópico, o en su compensatoria: la mediana de
    # prematuridad repetía el último R-R, y el ectópico y su pausa entraban en
    # el SDNN y el RMSSD.
    "termina_en_un_ectopico": (-0.300, True),
    "termina_en_la_compensatoria": (-1.500, True),
}


def _bloques_contra_entero(
    desfase: float, ectopico: bool
) -> tuple[pipeline.PipelineResult, dict[str, float], list[tuple[int, int, str]]]:
    """El análisis entero y dos bloques de producción (60 s de contexto, 30 de
    contexto derecho) con el latido de referencia a `desfase` s del borde.

    La señal se recorta por adelante para mover los latidos y no el borde: el
    borde queda en 100 s, múltiplo de la ventana, así la grilla de calidad es
    la misma de una sola vez y en bloques.
    """
    base = synth_ecg(duration_s=200.0, bpm=66.0, ectopic_every=6)
    borde, contexto, derecho = 100 * SR, 60 * SR, 30 * SR
    referencia = next(
        int(r)
        for indice, r in enumerate(base.rpeaks)
        if r > 105 * SR and (indice % 6 == 5) == ectopico
    )
    corte = referencia - borde - int(round(desfase * SR))
    señal, flags = base.signal_mv[corte:], base.flags[corte:]

    entero = _analyze(señal, flags)
    primero = _analyze(
        señal[: borde + derecho], flags[: borde + derecho], lookahead=derecho, fold_key="a"
    )
    desde = borde - contexto
    segundo = _analyze(
        señal[desde:],
        flags[desde:],
        start=desde,
        context=contexto,
        bank=primero.bank,
        fold_key="b",
    )
    assert segundo.bank.beats_seen == entero.bank.beats_seen
    return entero, combine_totals(primero.totals, segundo.totals), _tramos(primero, segundo)


def _tramos(*results: pipeline.PipelineResult) -> list[tuple[int, int, str]]:
    """`(inicio, fin, nivel)` de calidad, fundiendo los contiguos del mismo nivel.

    Cada bloque escribe sus intervalos sin cruzar el borde; los que se tocan
    con el mismo nivel son el mismo tramo, como los funde la lectura.
    """
    tramos: list[tuple[int, int, str]] = []
    for result in results:
        for interval, _ in result.quality_intervals:
            inicio, fin = interval.start_sample, interval.start_sample + interval.length_samples
            if tramos and tramos[-1][1] == inicio and tramos[-1][2] == interval.level.value:
                inicio = tramos.pop()[0]
            tramos.append((inicio, fin, interval.level.value))
    return tramos


@pytest.mark.parametrize("caso", sorted(_BORDES))
def test_el_borde_de_bloque_no_cambia_los_totales_caiga_donde_caiga(caso: str) -> None:
    """Con el contexto derecho, el final de un bloque deja de ser un borde duro:
    lo que el bloque cuenta ahí lo ve como lo vería el análisis de corrido. Los
    totales de los dos bloques son **exactamente** los del análisis entero, con
    el borde encima de un QRS, pegado a un R o justo después de un ectópico."""
    entero, sumados, ventanas = _bloques_contra_entero(*_BORDES[caso])

    for key in (
        "analyzedSamples",
        "beats",
        "nnCount",
        "diffCount",
        "nn50Count",
        "windows",
        "windowsGood",
    ):
        assert sumados[key] == entero.totals[key], key
    for key in ("nnSumMs", "nnSumSqMs2", "diffSumSqMs2", "hrMinBpm", "hrMaxBpm"):
        assert sumados[key] == pytest.approx(entero.totals[key], rel=1e-9), key
    assert ventanas == _tramos(entero)


def test_dos_bloques_consecutivos_suman_lo_mismo_que_un_analisis_entero() -> None:
    """La razón de ser de los acumuladores. El borde cae en señal limpia, medio
    segundo después de un latido: cada R-R y cada latido se cuenta en uno solo
    de los dos bloques, el de su latido final."""
    ecg = synth_ecg(duration_s=240.0, ectopic_every=7)
    boundary, context = 120 * SR, 60 * SR
    entero = _analyze(ecg.signal_mv, ecg.flags)

    primero = _analyze(ecg.signal_mv[:boundary], ecg.flags[:boundary], fold_key="a")
    desde = boundary - context
    segundo = _analyze(
        ecg.signal_mv[desde:],
        ecg.flags[desde:],
        start=desde,
        context=context,
        bank=primero.bank,
        fold_key="b",
    )
    sumados = combine_totals(primero.totals, segundo.totals)

    for key in ("analyzedSamples", "beats", "nnCount", "diffCount", "nn50Count", "windows"):
        assert sumados[key] == entero.totals[key], key
    assert sumados["nnSumMs"] == pytest.approx(entero.totals["nnSumMs"], rel=1e-9)
    assert sumados["nnSumSqMs2"] == pytest.approx(entero.totals["nnSumSqMs2"], rel=1e-9)
    assert sumados["diffSumSqMs2"] == pytest.approx(entero.totals["diffSumSqMs2"], rel=1e-9)
    # El banco ve los mismos latidos de una vez o en dos bloques.
    assert segundo.bank.beats_seen == entero.bank.beats_seen

    resumen, referencia = summary_from_totals(sumados), summary_from_totals(entero.totals)
    for key in ("meanNNms", "sdnnMs", "rmssdMs", "pnn50", "minBpm", "maxBpm", "goodRatio"):
        assert resumen[key] == pytest.approx(referencia[key], abs=1e-6), key


def test_los_totales_de_un_bloque_coinciden_con_hrv_summary() -> None:
    """`hrv_summary` es la cuenta directa con numpy; los totales son momentos
    sumables. Sobre un solo bloque sin contexto tienen que dar lo mismo, y es lo
    que verifica que las sumas estén bien armadas."""
    ecg = synth_ecg(duration_s=300.0, ectopic_every=7)
    result = _analyze(ecg.signal_mv, ecg.flags)

    config = _config()
    cleaned = clean_signal(ecg.signal_mv, SR)
    detected = detect_rpeaks(cleaned, SR)
    report = assess_quality(
        ecg.signal_mv,
        ecg.flags,
        cleaned,
        compensate_firmware_peaks(
            firmware_rpeaks(ecg.flags),
            lag_samples=config.quality.firmware_lag_samples,
            refractory_samples=config.quality.firmware_refractory_samples,
        ),
        detected,
        sample_rate=SR,
        thresholds=config.quality,
    )
    referencia = hrv_summary(build_rr(detected, report.analyzable, SR))
    resumen = summary_from_totals(result.totals)
    for key in ("meanNNms", "sdnnMs", "rmssdMs"):
        assert resumen[key] == pytest.approx(referencia[key], abs=2e-3), key
    assert resumen["pnn50"] == pytest.approx(referencia["pnn50"], abs=1e-6)
    # Y `metrics` sigue trayendo las claves de antes, ahora desde los totales.
    for key in ("meanNNms", "sdnnMs", "rmssdMs", "pnn50", "goodRatio", "windows", "beats"):
        assert result.metrics[key] == resumen[key]


# --------------------------------------------------------------------------- #
# Empalmes
# --------------------------------------------------------------------------- #


def test_exclude_splices_ubica_el_empalme_por_su_inicio_y_no_por_su_largo() -> None:
    """El largo de un `frame_gap` es tiempo perdido, no muestras del buffer: si
    se usara para ubicarlo, un hueco de 30 s borraría 30 s de señal real."""
    n = 20 * SR
    guard = SPLICE_GUARD_MS * SR // 1000
    span = SPLICE_SPAN_MS * SR // 1000
    masked = exclude_splices(np.ones(n, dtype=bool), ((5 * SR, 30 * SR),), SR)
    assert np.flatnonzero(~masked).tolist() == list(range(5 * SR - guard, 5 * SR + span + guard))

    # Los que caen fuera de la señal se ignoran; los del borde se recortan.
    fuera = exclude_splices(np.ones(n, dtype=bool), ((n, 100), (-span - 1, 100)), SR)
    assert fuera.all()
    borde = exclude_splices(np.ones(n, dtype=bool), ((0, 100),), SR)
    assert not borde[: span + guard].any() and borde[span + guard :].all()


def test_el_rr_que_cruza_un_empalme_queda_invalido() -> None:
    analyzable = exclude_splices(np.ones(4_000, dtype=bool), ((1_000, 750),), SR)
    # Fuera de la máscara: [900, 1.600). El R de 1.500 cae adentro e invalida
    # los dos intervalos que lo tocan, aunque el segundo termine afuera.
    rr = build_rr(np.array([500, 880, 1_500, 2_200], dtype=np.int64), analyzable, SR)
    assert rr.valid.tolist() == [True, False, False]


def test_un_empalme_invalida_los_rr_que_lo_tocan_y_saca_los_latidos_a_caballo() -> None:
    """Un `frame_gap` entre los latidos de 59,5 s y 60,5 s. Sin la exclusión, el
    R-R que lo cruza mediría un segundo que en realidad fue un segundo más el
    hueco, y el latido de 60,5 s se compararía contra la plantilla con su
    ventana partida en dos instantes."""
    ecg = synth_ecg(duration_s=120.0)
    sin_hueco = _analyze(ecg.signal_mv, ecg.flags)
    con_hueco = _analyze(ecg.signal_mv, ecg.flags, gaps=((60 * SR, 15 * SR),))

    # [59,8 s, 61,2 s) fuera de la máscara: el R de 60,5 s cae adentro, los R-R
    # 59,5→60,5 y 60,5→61,5 lo tocan, y el 61,5→62,5 ya no.
    assert con_hueco.totals["beats"] == sin_hueco.totals["beats"] - 1
    assert con_hueco.totals["nnCount"] == sin_hueco.totals["nnCount"] - 2
    assert con_hueco.metrics["analyzedBeats"] == sin_hueco.metrics["analyzedBeats"] - 1
    # Las ventanas de calidad no cambian: el empalme no dice nada de la señal.
    assert con_hueco.quality_intervals == sin_hueco.quality_intervals


def test_un_empalme_en_medio_de_una_taquicardia_no_la_parte() -> None:
    """Un `frame_gap` de 50 ms a mitad de 56 s a 125 lpm invalida dos o tres
    R-R. Cortado ahí, el episodio quedaba en dos mitades de menos de 30 s y
    ninguna llegaba al mínimo: la taquicardia desaparecía. Un tramo corto que
    solo tiene R-R inválidos se puentea; sus R-R no entran en las frecuencias."""
    ecg = synth_ecg(duration_s=56.0, bpm=125.0)
    sin_hueco = _analyze(ecg.signal_mv, ecg.flags)
    con_hueco = _analyze(ecg.signal_mv, ecg.flags, gaps=((28 * SR, 25),))

    def taquicardias(result: pipeline.PipelineResult) -> list[Finding]:
        return [finding for finding in result.findings if finding.kind == "tachycardia"]

    [referencia] = taquicardias(sin_hueco)
    [puenteada] = taquicardias(con_hueco)
    assert (puenteada.start_sample, puenteada.length_samples) == (
        referencia.start_sample,
        referencia.length_samples,
    )
    assert puenteada.metadata["peakBpm"] == pytest.approx(125.0, abs=1.0)


def test_un_hueco_largo_de_senal_ilegible_si_corta_el_episodio() -> None:
    """El puente es para empalmes, no para señal que no se pudo leer: 20 s de
    ruido en medio de 50 s de taquicardia la dejan en dos tramos de 15 s, y
    ninguno es una taquicardia sostenida."""
    ecg = synth_ecg(duration_s=50.0, bpm=125.0)
    ruidosa = ecg.signal_mv.copy()
    ruidosa[15 * SR : 35 * SR] = np.random.default_rng(3).normal(0.0, 0.6, 20 * SR)
    result = _analyze(ruidosa, ecg.flags)
    assert not [finding for finding in result.findings if finding.kind == "tachycardia"]


def test_dos_pausas_seguidas_fundidas_no_cuentan_dos_veces_el_r_del_medio() -> None:
    def pausa(desde: int, hasta: int) -> Finding:
        return Finding(
            kind="pause",
            event_type=ECGEventType.PAUSE,
            severity=ECGEventSeverity.HIGH,
            start_sample=desde,
            length_samples=hasta - desde,
            dedupe_key=f"pause:{desde}",
            beat_count=2,
            metadata={"pauseSeconds": (hasta - desde) / SR},
            beat_samples=(desde, hasta),
        )

    [fundida] = apply_refractory(
        [pausa(0, 1_400), pausa(1_400, 2_900)], sample_rate=SR, refractory_seconds=10.0
    )
    assert fundida.beat_samples == (0, 1_400, 2_900)
    assert fundida.beat_count == 3


# --------------------------------------------------------------------------- #
# Acumuladores contra numpy
# --------------------------------------------------------------------------- #


def _series(chunks: list[np.ndarray]) -> tuple[RRSeries, np.ndarray]:
    """Serie R-R con `chunks` de intervalos válidos separados por uno inválido.

    Los R se ubican a 1 kHz para que el intervalo en muestras sea el intervalo
    en ms sin redondeo.
    """
    intervals: list[float] = []
    valid: list[bool] = []
    for index, chunk in enumerate(chunks):
        if index:
            intervals.append(4_000.0)
            valid.append(False)
        intervals.extend(chunk.tolist())
        valid.extend([True] * chunk.size)
    rpeaks = np.concatenate(([0], np.cumsum(np.round(intervals)))).astype(np.int64)
    rr = RRSeries(
        rpeaks=rpeaks,
        rr_seconds=(np.diff(rpeaks) / 1000.0).astype(np.float32),
        valid=np.array(valid, dtype=bool),
        sample_rate=1_000,
    )
    return rr, np.ones(int(rpeaks[-1]) + 1, dtype=bool)


def _totals(chunks: list[np.ndarray]) -> dict[str, float]:
    rr, analyzable = _series(chunks)
    return block_totals(
        rr, (), analyzable, context_samples=0, n_samples=analyzable.size, sample_rate=1_000
    )


def _numpy_reference(chunks: list[np.ndarray]) -> dict[str, float]:
    rounded = [np.round(chunk) for chunk in chunks]
    nn = np.concatenate(rounded)
    diffs = np.concatenate([np.diff(chunk) for chunk in rounded])
    return {
        "meanNNms": float(np.mean(nn)),
        "sdnnMs": float(np.std(nn, ddof=1)),
        "rmssdMs": float(np.sqrt(np.mean(diffs**2))),
        "pnn50": float(np.mean(np.abs(diffs) > 50.0)),
    }


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_los_acumuladores_dan_lo_mismo_que_numpy_y_combinan_en_cualquier_orden(
    seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    # Recortado a ±15 %: más corto que el 80 % de la mediana local sería un
    # ectópico y saldría de la serie NN (`hrv.normal_intervals`), que no es lo
    # que mide este test sino las sumas.
    bloques = [
        [
            np.clip(rng.normal(800, 70, int(rng.integers(50, 400))), 680.0, 920.0)
            for _ in range(int(rng.integers(1, 4)))
        ]
        for _ in range(3)
    ]
    a, b, c = (_totals(chunks) for chunks in bloques)

    izquierda = combine_totals(combine_totals(a, b), c)
    derecha = combine_totals(a, combine_totals(b, c))
    assert izquierda.keys() == derecha.keys()
    for key in izquierda:
        assert izquierda[key] == pytest.approx(derecha[key], rel=1e-12), key

    referencia = _numpy_reference([chunk for chunks in bloques for chunk in chunks])
    resumen = summary_from_totals(izquierda)
    for key, value in referencia.items():
        assert resumen[key] == pytest.approx(value, abs=1e-3), key


def test_la_frecuencia_minima_y_maxima_combinan_por_extremo_y_toleran_ausencias() -> None:
    a = {"beats": 10.0, "hrMinBpm": 55.0, "hrMaxBpm": 90.0}
    b = {"beats": 5.0}
    c = {"beats": 1.0, "hrMinBpm": 48.0, "hrMaxBpm": 85.0, "reason.ok": 3.0}
    combined = combine_totals(combine_totals(a, b), c)
    assert combined == {"beats": 16.0, "hrMinBpm": 48.0, "hrMaxBpm": 90.0, "reason.ok": 3.0}
    assert combine_totals(b, a)["hrMinBpm"] == 55.0
    # Sin intervalos no hay métricas inventadas en cero.
    resumen = summary_from_totals(b)
    assert {"meanNNms", "sdnnMs", "rmssdMs", "pnn50", "minBpm", "maxBpm"}.isdisjoint(resumen)
    assert resumen["goodRatio"] == 0.0


def test_hrv_summary_no_diferencia_a_traves_de_un_intervalo_invalido() -> None:
    """Antes diferenciaba la serie de válidos concatenada: el último R-R antes
    de un tramo descartado contra el primero después, como si fueran vecinos."""
    rr, _ = _series([np.array([800.0, 810.0]), np.array([1_200.0, 1_190.0])])
    summary = hrv_summary(rr)
    # Dos diferencias de 10 ms; la de 800→1.200 no existe.
    assert summary["rmssdMs"] == pytest.approx(10.0)
    assert summary["pnn50"] == 0.0


def test_los_intervalos_que_tocan_un_ectopico_no_entran_en_la_variabilidad() -> None:
    """60 lpm exactos con un ectópico (60 % del R-R) cada doce latidos y su pausa
    compensadora: la variabilidad sinusal es cero. Contando los R-R de los
    ectópicos daba un RMSSD de ~280 ms."""
    intervalos: list[float] = []
    for latido in range(240):
        if latido % 12 == 6:
            intervalos.extend([600.0, 1_400.0])
        else:
            intervalos.append(1_000.0)
    rr, analyzable = _series([np.array(intervalos)])
    totals = block_totals(
        rr, (), analyzable, context_samples=0, n_samples=analyzable.size, sample_rate=1_000
    )
    resumen = summary_from_totals(totals)
    assert resumen["sdnnMs"] == 0.0
    assert resumen["rmssdMs"] == 0.0
    assert resumen["pnn50"] == 0.0
    assert resumen["meanNNms"] == 1_000.0
    # Los dos intervalos de cada ectópico salen; el resto entra.
    ectopicos = sum(1 for latido in range(240) if latido % 12 == 6)
    assert totals["nnCount"] == len(intervalos) - 2 * ectopicos
    assert hrv_summary(rr)["rmssdMs"] == 0.0


def test_la_frecuencia_por_ventana_exige_cinco_intervalos_contados() -> None:
    rr, analyzable = _series([np.full(20, 1_000.0)])
    ventanas = (
        # 0-4,5 s: cuatro R-R terminan adentro, no alcanza.
        QualityWindow(0, 4_500, SignalQualityLevel.GOOD, "ok"),
        # 4,5-12 s: siete R-R a 60 lpm.
        QualityWindow(4_500, 7_500, SignalQualityLevel.GOOD, "ok"),
        # Una ventana mala no entra aunque tenga latidos.
        QualityWindow(12_000, 8_001, SignalQualityLevel.BAD, "ksqi"),
    )
    totals = block_totals(
        rr, ventanas, analyzable, context_samples=0, n_samples=analyzable.size, sample_rate=1_000
    )
    assert totals["hrMinBpm"] == totals["hrMaxBpm"] == pytest.approx(60.0)
    assert totals["reason.ksqi"] == 1.0
    assert totals["windowsBad"] == 1.0


# --------------------------------------------------------------------------- #
# Refractariedad
# --------------------------------------------------------------------------- #


def test_la_refractariedad_recalcula_la_duracion_del_episodio_fundido() -> None:
    """Fundidas, dos taquicardias declaraban la duración de la primera sola."""

    def taquicardia(start_s: float, length_s: float, peak: float) -> Finding:
        start, length = int(start_s * SR), int(length_s * SR)
        return Finding(
            kind="tachycardia",
            event_type=ECGEventType.TACHYCARDIA,
            severity=ECGEventSeverity.MEDIUM,
            start_sample=start,
            length_samples=length,
            dedupe_key=f"tachycardia:{start}",
            beat_count=int(length_s * 2),
            metadata={"peakBpm": peak, "medianBpm": 110.0, "durationSeconds": length_s},
        )

    [fundida] = apply_refractory(
        [taquicardia(0.0, 40.0, 120.0), taquicardia(45.0, 35.0, 140.0)],
        sample_rate=SR,
        refractory_seconds=10.0,
    )
    assert fundida.length_samples == 80 * SR
    assert fundida.metadata["durationSeconds"] == 80.0
    assert fundida.metadata["peakBpm"] == 140.0
    # Una mediana no se recompone de dos: la del primero describía solo el arranque.
    assert "medianBpm" not in fundida.metadata
    assert fundida.beat_count == 150
