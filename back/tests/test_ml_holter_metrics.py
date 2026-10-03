"""Métricas del informe sobre latidos sintéticos: reglas de RR, evidencia y ST."""

import numpy as np
import pytest

from app.ml.beats import BEAT_DTYPE
from app.ml.holter_metrics import PAUSE_MS, TimelineRun, compute_holter_metrics

RATE = 500
START_EPOCH = 1_760_000_400_000  # una hora en punto: 2025-10-09 09:00 UTC


def _beats(rr_s: np.ndarray, *, start_sample: int = 0, st: np.ndarray | None = None) -> np.ndarray:
    samples = start_sample + np.round(np.concatenate(([0.0], np.cumsum(rr_s))) * RATE)
    beats = np.empty(samples.size, dtype=BEAT_DTYPE)
    beats["sample_index"] = samples.astype(np.int64)
    beats["st_mv"] = 0.0 if st is None else st
    return beats


def _run(start: int, count: int, epoch: int = START_EPOCH) -> TimelineRun:
    return TimelineRun(start, count, epoch, epoch + count * 1000 // RATE)


def _metrics(beats: np.ndarray, runs: list[TimelineRun], **kwargs: object) -> dict:
    total = max(run.start_sample + run.sample_count for run in runs)
    return compute_holter_metrics(
        beats,
        runs,
        kwargs.get("exclusions", []),  # type: ignore[arg-type]
        kwargs.get("breaks", []),  # type: ignore[arg-type]
        RATE,
        total,
    )


def test_fc_constante_da_promedio_minimo_y_maximo_iguales() -> None:
    beats = _beats(np.full(749, 0.8))  # 10 min a 75 lpm
    metrics = _metrics(beats, [_run(0, 600 * RATE)])

    heart = metrics["heartRate"]
    assert metrics["status"] == "ok"
    assert heart["totalBeats"] == 750
    assert heart["averageBpm"] == 75
    assert heart["min"]["value"] == heart["max"]["value"] == 75
    assert heart["abnormalBeats"] is None and heart["abnormalPerThousand"] is None
    assert metrics["supraventricular"] is None and metrics["ventricular"] is None
    assert metrics["pauses"]["count"] == 0


def test_fc_minima_y_maxima_llevan_la_hora_del_tramo_donde_ocurren() -> None:
    rr = np.concatenate([np.full(300, 0.8), np.full(60, 0.5), np.full(300, 0.8), np.full(40, 1.2)])
    beats = _beats(rr)
    metrics = _metrics(beats, [_run(0, int(rr.sum() * RATE) + RATE)])

    high, low = metrics["heartRate"]["max"], metrics["heartRate"]["min"]
    assert high["value"] == 120 and low["value"] == 50
    fast_start = 300 * 0.8 * 1000
    assert fast_start <= high["epochMs"] - START_EPOCH <= fast_start + 60 * 500
    assert low["epochMs"] - START_EPOCH >= (300 * 0.8 + 60 * 0.5 + 300 * 0.8) * 1000
    assert low["sampleIndex"] == round((low["epochMs"] - START_EPOCH) * RATE / 1000)


def test_pausa_dentro_de_un_tramo_se_cuenta_con_su_hora() -> None:
    rr = np.full(200, 0.8)
    rr[100] = 2.5
    metrics = _metrics(_beats(rr), [_run(0, 200 * RATE)])

    pauses = metrics["pauses"]
    assert pauses["thresholdMs"] == PAUSE_MS
    assert pauses["count"] == 1
    assert pauses["longest"]["durationMs"] == 2500
    assert pauses["longest"]["epochMs"] == START_EPOCH + 100 * 800


def test_corte_de_tramo_hueco_o_exclusion_no_son_pausas() -> None:
    rr = np.full(200, 0.8)
    rr[50] = rr[100] = rr[150] = 3.0
    beats = _beats(rr)
    s = beats["sample_index"]
    first_run_end = int(s[51])
    runs = [
        _run(0, first_run_end),
        _run(first_run_end, 200 * RATE, START_EPOCH + 3_600_000),
    ]
    metrics = _metrics(
        beats,
        runs,
        breaks=[int(s[100]) + 10],
        exclusions=[(int(s[150]) + 10, int(s[151]) - 10)],
    )

    assert metrics["pauses"]["count"] == 0
    assert metrics["analysis"]["rrIntervals"] == 200 - 3


def test_latidos_dentro_de_una_exclusion_no_se_cuentan() -> None:
    beats = _beats(np.full(149, 0.8))
    s = beats["sample_index"]
    metrics = _metrics(beats, [_run(0, 130 * RATE)], exclusions=[(int(s[10]), int(s[20]))])

    assert metrics["heartRate"]["totalBeats"] == 140
    assert metrics["analysis"]["excludedMs"] == 8000


def test_sin_latidos_suficientes_queda_sin_datos() -> None:
    metrics = _metrics(_beats(np.empty(0)), [_run(0, 10 * RATE)])

    assert metrics["status"] == "insufficient_data"
    assert metrics["heartRate"] is None and metrics["hrvTime"] is None


def test_el_filtro_nn_descarta_un_latido_prematuro() -> None:
    rr = np.full(400, 0.8)
    rr[200], rr[201] = 0.5, 1.1  # extrasístole con pausa compensatoria
    metrics = _metrics(_beats(rr), [_run(0, 400 * RATE)])

    assert metrics["analysis"]["nnIntervals"] == 398
    assert metrics["hrvTime"]["sdnnMs"] == 0.0


def test_tendencia_horaria_e_histograma() -> None:
    rr = np.concatenate([np.full(4500, 0.8), np.full(3600, 1.0)])  # 1 h a 75 y 1 h a 60
    metrics = _metrics(_beats(rr), [_run(0, int(rr.sum() * RATE) + RATE)])

    hours = metrics["hourly"]
    assert [hour["avgBpm"] for hour in hours[:2]] == [75, 60]
    assert hours[0]["hourStartEpochMs"] == START_EPOCH
    histogram = metrics["rrHistogram"]
    assert sum(histogram["counts"]) == metrics["analysis"]["nnIntervals"]
    assert histogram["counts"][(800 - 300) // 50] == 4500


def test_st_detecta_un_episodio_de_elevacion_y_otro_de_depresion() -> None:
    rr = np.full(75 * 10, 0.8)  # 10 min
    st = np.zeros(rr.size + 1)
    st[75 * 3 : 75 * 5] = 0.2  # minutos 3 y 4
    st[75 * 7 : 75 * 8] = -0.15  # minuto 7
    metrics = _metrics(_beats(rr, st=st), [_run(0, 601 * RATE)])

    channel = metrics["st"][0]
    assert channel["label"] == "Canal 1 (LL-RA)"
    elevation, depression = channel["elevation"], channel["depression"]
    assert elevation["episodes"] == 1 and elevation["durationSeconds"] == 120
    assert elevation["maxDeviation"]["value"] == pytest.approx(0.2)
    assert elevation["maxSlopeMvPerMin"] == pytest.approx(0.2)
    assert depression["episodes"] == 1 and depression["durationSeconds"] == 60
    assert depression["maxDeviation"]["value"] == pytest.approx(0.15)


def test_st_no_inventa_un_minuto_con_latidos_concentrados_o_un_hueco() -> None:
    for seconds in ([0, 1, 2, 3, 4], [0, 1, 2, 54, 55, 56]):
        beats = np.empty(len(seconds), dtype=BEAT_DTYPE)
        beats["sample_index"] = np.asarray(seconds) * RATE
        beats["st_mv"] = 0.2

        metrics = _metrics(beats, [_run(0, 60 * RATE)])

        st = metrics["st"][0]
        assert st["analyzedMinutes"] == 0
        assert st["elevation"]["episodes"] == 0
        assert st["elevation"]["durationSeconds"] == 0


def test_st_separa_episodios_si_faltan_latidos_entre_minutos() -> None:
    seconds = np.concatenate((np.arange(0, 56), np.arange(64, 120)))
    beats = np.empty(seconds.size, dtype=BEAT_DTYPE)
    beats["sample_index"] = seconds * RATE
    beats["st_mv"] = 0.2

    metrics = _metrics(beats, [_run(0, 120 * RATE)])

    assert metrics["st"][0]["elevation"]["episodes"] == 2


def test_resultado_es_determinista() -> None:
    rng = np.random.default_rng(1)
    rr = 0.8 + 0.05 * rng.standard_normal(3000)
    runs = [_run(0, int(rr.sum() * RATE) + RATE)]
    assert _metrics(_beats(rr), runs) == _metrics(_beats(rr), runs)
