"""Métricas del informe sobre latidos sintéticos: reglas de RR, evidencia y ST."""

import numpy as np
import pytest

from app.ml.beat_labels import CLASSIFIER_VERSION, BeatLabelStream, build_records
from app.ml.beats import BEAT_DTYPE
from app.ml.holter_contracts import BeatRelabel
from app.ml.holter_metrics import (
    ALGORITHM_VERSION,
    ALGORITHM_VERSION_WITHOUT_CLASSIFICATION,
    PAUSE_MS,
    DoctorCorrections,
    TimelineRun,
    compute_holter_metrics,
)

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


def _pausa_en_ruido(
    engine_pauses: list[tuple[int, int]], noise: list[tuple[int, int]] | None
) -> dict:
    rr = np.full(200, 0.8)
    rr[100] = 3.0
    beats = _beats(rr)
    total = int(rr.sum() * RATE) + RATE
    return compute_holter_metrics(
        beats, [_run(0, total)], [], [], RATE, total, noise=noise, engine_pauses=engine_pauses
    )["pauses"]


def test_una_pausa_en_ruido_que_el_motor_no_afirmo_va_aparte() -> None:
    """Las pausas falsas de `04c72848` y `969174a3` (`INTEGRACION.md`, pendiente 3)."""
    opens = round(100 * 0.8 * RATE)
    pauses = _pausa_en_ruido([], [(opens - RATE, opens + 4 * RATE)])

    assert pauses["count"] == 0 and pauses["longest"] is None and pauses["items"] == []
    assert pauses["noisyCount"] == 1
    assert pauses["noisyItems"][0]["durationMs"] == 3000


def test_sin_el_veredicto_del_motor_la_pausa_cuenta_como_siempre() -> None:
    pauses = _pausa_en_ruido([], None)

    assert pauses["count"] == 1 and pauses["noisyCount"] == 0


def test_la_pausa_del_motor_reemplaza_a_los_pedazos_del_detector() -> None:
    """Dentro de una asistolia el detector encuentra "latidos" en el ruido y
    la parte; la del motor la delimita entera y es la que queda."""
    opens = round(100 * 0.8 * RATE)
    noise = [(opens - RATE, opens + 4 * RATE)]
    pauses = _pausa_en_ruido([(opens - 2 * RATE, opens + 3 * RATE)], noise)

    assert pauses["count"] == 1 and pauses["noisyCount"] == 0
    assert pauses["longest"]["durationMs"] == 5000
    assert pauses["longest"]["sampleIndex"] == opens - 2 * RATE
    assert pauses["longest"]["epochMs"] == START_EPOCH + (opens - 2 * RATE) * 1000 // RATE


def _dos_pausas_en_ruido(**kwargs: object) -> dict:
    """Pausas de 4 y 3 s separadas por 4 latidos, todo en ruido del motor."""
    rr = np.full(200, 1.0)
    rr[59], rr[64] = 4.0, 3.0
    beats = _beats(rr)
    total = int(rr.sum() * RATE) + RATE
    return compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        noise=[(58 * RATE, 75 * RATE)],
        **kwargs,  # type: ignore[arg-type]
    )["pauses"]


def test_un_evento_fundido_del_motor_confirma_y_no_inventa_una_pausa_larga() -> None:
    """La refractariedad de 10 s funde las dos en un evento de 59 a 70 s. Su
    largo no es una pausa: contarlo daba una de 11 s donde hubo una de 4 y otra
    de 3."""
    pauses = _dos_pausas_en_ruido(engine_episodes=[(59 * RATE, 70 * RATE)])

    assert pauses["count"] == 2 and pauses["noisyCount"] == 0
    assert [item["durationMs"] for item in pauses["items"]] == [4000, 3000]


def test_un_evento_fundido_no_confirma_lo_que_no_toca() -> None:
    pauses = _dos_pausas_en_ruido(engine_episodes=[(59 * RATE, 63 * RATE)])

    assert pauses["count"] == 1 and pauses["longest"]["durationMs"] == 4000
    assert pauses["noisyCount"] == 1 and pauses["noisyItems"][0]["durationMs"] == 3000


def test_los_latidos_del_pan_tompkins_se_declaran_y_calculan_como_la_version_3() -> None:
    """Un estudio analizado antes de la 4 y sin `--reset`: la nota de método no
    puede nombrar a NeuroKit, y sus pausas en ruido cuentan como en la 3."""
    pauses = _dos_pausas_en_ruido(legacy_beats=True, engine_pauses=[(59 * RATE, 63 * RATE)])
    assert pauses["count"] == 2 and pauses["noisyCount"] == 0

    rr = np.full(100, 0.8)
    runs = [_run(0, int(rr.sum() * RATE) + RATE)]
    total = runs[0].sample_count

    def version(**kwargs: object) -> int:
        result = compute_holter_metrics(_beats(rr), runs, [], [], RATE, total, **kwargs)  # type: ignore[arg-type]
        return int(result["analysis"]["algorithmVersion"])

    assert version(legacy_beats=True, noise=[]) == 3
    assert version(legacy_beats=True) == 1
    assert version(noise=[]) == 5
    assert version() == 4


# --- S y V desde las etiquetas del motor (req. 4) --------------------------- #


def _labelled(
    pattern: str, *, rr_s: float = 0.8, from_sample: int = 0, until: int | None = None
) -> tuple[np.ndarray, BeatLabelStream]:
    """Latidos a RR constante y su flujo de etiquetas; un carácter por latido.

    Un guion bajo es un latido `Q` (no clasificable) en el flujo.
    """
    beats = _beats(np.full(len(pattern) - 1, rr_s))
    codes = np.array([ord("Q" if c == "_" else c) for c in pattern], dtype=np.uint8)
    n = codes.size
    records = build_records(
        beats["sample_index"],
        codes,
        np.zeros(n, dtype=np.uint8),
        np.full(n, -20, dtype=np.int64),
        np.full(n, 25, dtype=np.int64),
        np.zeros(n, dtype=np.int64),
        np.full(n, 1.0, dtype=np.float32),
        qrs_measured=np.ones(n, dtype=bool),
        p_detected=np.zeros(n, dtype=bool),
    )
    total = int(beats["sample_index"][-1]) + RATE
    stream = BeatLabelStream(
        records=records,
        from_sample=from_sample,
        until_sample=total if until is None else until,
        classifier=CLASSIFIER_VERSION,
    )
    return beats, stream


def _with_labels(pattern: str, **kwargs: object) -> dict:
    beats, stream = _labelled(pattern)
    total = int(beats["sample_index"][-1]) + RATE
    return compute_holter_metrics(
        beats,
        [_run(0, total)],
        kwargs.pop("exclusions", []),  # type: ignore[arg-type]
        [],
        RATE,
        total,
        noise=[],
        labels=stream,
        **kwargs,  # type: ignore[arg-type]
    )


def test_las_metricas_s_y_v_cuentan_cada_patron_con_su_evidencia() -> None:
    #       aislada  par   corrida   bigeminismo (3 ciclos)  trigeminismo (3 ciclos)
    pattern = "NNVNN" + "NNVVNN" + "NNVVVNN" + "NNNVNVNVNN" + "NNNNVNNVNNVNNN" + "NNSNNNSSN"
    metrics = _with_labels(pattern)

    v, s = metrics["ventricular"], metrics["supraventricular"]
    assert metrics["ectopyUnavailableReason"] is None
    assert v["total"] == 1 + 2 + 3 + 3 + 3
    assert v["pairs"] == {"episodes": 1, "beats": 2}
    assert v["runs"] == {"episodes": 1, "beats": 3}
    assert v["bigeminy"] == {"episodes": 1, "beats": 3}
    assert v["trigeminy"] == {"episodes": 1, "beats": 3}
    # Las aisladas incluyen las de un bigeminismo o trigeminismo (ectopy.py).
    assert v["single"] == 1 + 3 + 3
    assert s["total"] == 3 and s["single"] == 1 and s["pairs"]["episodes"] == 1
    classified = len(pattern)
    assert v["perThousand"] == round(1000 * v["total"] / classified, 1)
    heart = metrics["heartRate"]
    assert heart["abnormalBeats"] == v["total"] + s["total"]
    assert heart["abnormalPerThousand"] == round(1000 * (v["total"] + s["total"]) / classified, 1)
    assert metrics["analysis"]["classifiedBeats"] == classified
    assert metrics["analysis"]["unclassifiedBeats"] == 0
    # La corrida más larga: tres latidos, con la muestra del primero.
    assert v["longestRun"]["value"] == 3
    assert v["longestRun"]["durationMs"] == 1600
    assert v["longestRun"]["epochMs"] >= START_EPOCH


def test_el_maximo_por_minuto_es_la_ventana_de_60_s_con_mas_latidos() -> None:
    # 0,8 s de RR: 75 latidos por minuto. Dos V en el minuto 0, cuatro en el minuto 1.
    pattern = "N" * 10 + "VN" + "VN" + "N" * 80 + "VNVNVNVN" + "N" * 20
    v = _with_labels(pattern)["ventricular"]

    assert v["maxPerMinute"]["value"] == 4
    assert v["maxPerMinute"]["epochMs"] // 60_000 == (START_EPOCH + 60_000) // 60_000
    assert v["maxPerMinute"]["durationMs"] > 0


def test_el_maximo_por_minuto_no_se_parte_en_el_cambio_de_minuto() -> None:
    """Cinco V entre los 56 y los 62,4 s: con minutos de reloj eran 3 + 2."""
    pattern = "N" * 70 + "VNVNVNVNV" + "N" * 40
    v = _with_labels(pattern)["ventricular"]

    assert v["maxPerMinute"]["value"] == 5
    assert v["maxPerMinute"]["epochMs"] == START_EPOCH + 70 * 800
    assert v["maxPerMinute"]["durationMs"] == 8 * 800


def test_los_latidos_en_ruido_o_sin_clasificar_cortan_los_patrones_y_se_cuentan() -> None:
    metrics = _with_labels("NNVXVNNVQVNN")

    v = metrics["ventricular"]
    assert v["total"] == 4 and v["pairs"]["episodes"] == 0 and v["single"] == 4
    assert metrics["analysis"]["unclassifiedBeats"] == 2
    assert metrics["analysis"]["classifiedBeats"] == 10


def test_un_hueco_del_registro_corta_el_patron_entre_dos_v() -> None:
    beats, stream = _labelled("NNVVNN")
    total = int(beats["sample_index"][-1]) + RATE
    first_v = int(beats["sample_index"][2])
    metrics = compute_holter_metrics(
        beats, [_run(0, total)], [], [first_v + 100], RATE, total, noise=[], labels=stream
    )

    assert metrics["ventricular"]["pairs"]["episodes"] == 0
    assert metrics["ventricular"]["single"] == 2


@pytest.mark.parametrize(("with_labels", "legacy"), [(False, False), (True, True)])
def test_sin_etiquetas_o_con_latidos_viejos_s_y_v_no_estan_disponibles(
    with_labels: bool, legacy: bool
) -> None:
    beats, stream = _labelled("N" * 40)
    total = int(beats["sample_index"][-1]) + RATE
    metrics = compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        noise=[],
        legacy_beats=legacy,
        labels=stream if with_labels else None,
    )

    assert metrics["supraventricular"] is None and metrics["ventricular"] is None
    assert metrics["ectopyUnavailableReason"] == "BEAT_CLASSIFICATION_UNAVAILABLE"
    assert metrics["heartRate"]["abnormalBeats"] is None
    assert metrics["analysis"]["classifiedBeats"] == 0


def test_etiquetas_que_no_cubren_el_estudio_desde_el_inicio_son_parciales_no_ceros() -> None:
    beats, stream = _labelled("N" * 40, from_sample=RATE * 10)
    total = int(beats["sample_index"][-1]) + RATE
    metrics = compute_holter_metrics(
        beats, [_run(0, total)], [], [], RATE, total, noise=[], labels=stream
    )

    assert metrics["ventricular"] is None
    assert metrics["ectopyUnavailableReason"] == "BEAT_CLASSIFICATION_PARTIAL"


def test_etiquetas_sin_cobertura_todavia_son_pendientes() -> None:
    beats, stream = _labelled("N" * 40, until=0)
    total = int(beats["sample_index"][-1]) + RATE
    metrics = compute_holter_metrics(
        beats, [_run(0, total)], [], [], RATE, total, noise=[], labels=stream
    )

    assert metrics["ventricular"] is None
    assert metrics["ectopyUnavailableReason"] == "BEAT_CLASSIFICATION_PENDING"


def test_la_version_es_la_6_solo_si_se_uso_la_clasificacion() -> None:
    with_labels = _with_labels("N" * 20 + "V" + "N" * 20)
    beats, _ = _labelled("N" * 40)
    total = int(beats["sample_index"][-1]) + RATE
    without = compute_holter_metrics(beats, [_run(0, total)], [], [], RATE, total, noise=[])

    assert with_labels["analysis"]["algorithmVersion"] == ALGORITHM_VERSION == 6
    assert without["analysis"]["algorithmVersion"] == ALGORITHM_VERSION_WITHOUT_CLASSIFICATION


def test_reclasificar_los_v_de_un_tramo_a_n_baja_el_total_y_cuenta_los_latidos() -> None:
    beats, stream = _labelled("NNVNNNNVVNN")
    samples = beats["sample_index"]
    total = int(samples[-1]) + RATE
    # Los V del par (índices 7 y 8) pasan a N; el aislado (2) queda.
    correction = DoctorCorrections(
        relabels=(BeatRelabel(int(samples[7]), int(samples[8]) + 1, "V", "N"),),
        corrected_findings=1,
    )
    metrics = compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        noise=[],
        labels=stream,
        corrections=correction,
    )

    assert metrics["ventricular"]["total"] == 1
    assert metrics["ventricular"]["pairs"]["episodes"] == 0
    assert metrics["analysis"]["doctorCorrections"] == {
        "findings": 1,
        "noiseMs": 0,
        "relabeledBeats": 2,
    }


def test_reclasificar_de_v_a_s_mueve_los_latidos_de_recuadro() -> None:
    beats, stream = _labelled("NNVVNNN")
    samples = beats["sample_index"]
    total = int(samples[-1]) + RATE
    correction = DoctorCorrections(
        relabels=(BeatRelabel(0, total, "V", "S"),), corrected_findings=1
    )
    metrics = compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        noise=[],
        labels=stream,
        corrections=correction,
    )

    assert metrics["ventricular"]["total"] == 0
    assert metrics["supraventricular"]["total"] == 2
    assert metrics["supraventricular"]["pairs"]["episodes"] == 1


def test_el_ruido_del_medico_saca_los_latidos_y_gana_sobre_una_reetiqueta() -> None:
    beats, stream = _labelled("NNVNNVVNNN")
    samples = beats["sample_index"]
    total = int(samples[-1]) + RATE
    span = (int(samples[5]), int(samples[6]) + 1)
    correction = DoctorCorrections(
        noise_spans=(span,),
        relabels=(BeatRelabel(span[0], span[1], "V", "S"),),
        corrected_findings=2,
    )
    metrics = compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        noise=[],
        labels=stream,
        corrections=correction,
    )

    assert metrics["ventricular"]["total"] == 1
    assert metrics["supraventricular"]["total"] == 0
    assert metrics["heartRate"]["totalBeats"] == 8
    assert metrics["analysis"]["classifiedBeats"] == 8
    assert metrics["analysis"]["doctorCorrections"]["noiseMs"] > 0
    assert metrics["analysis"]["doctorCorrections"]["relabeledBeats"] == 0


def test_rechazar_un_ruido_del_motor_lo_devuelve_a_las_metricas() -> None:
    beats = _beats(np.full(99, 0.8))
    total = int(beats["sample_index"][-1]) + RATE
    noise = [(2000, 4000)]
    base = compute_holter_metrics(beats, [_run(0, total)], [], [], RATE, total, noise=noise)
    readable = compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        noise=noise,
        corrections=DoctorCorrections(readable_spans=((2000, 4000),), corrected_findings=1),
    )

    assert readable["heartRate"]["totalBeats"] > base["heartRate"]["totalBeats"]
    assert readable["analysis"]["excludedMs"] == 0


def test_rechazar_una_pausa_del_detector_la_saca_de_la_cuenta() -> None:
    rr = np.full(60, 0.8)
    rr[30] = 3.0
    beats = _beats(rr)
    total = int(beats["sample_index"][-1]) + RATE
    open_, close = int(beats["sample_index"][30]), int(beats["sample_index"][31])
    base = _metrics(beats, [_run(0, total)])
    rejected = compute_holter_metrics(
        beats,
        [_run(0, total)],
        [],
        [],
        RATE,
        total,
        corrections=DoctorCorrections(rejected_pause_spans=((open_, close),), corrected_findings=1),
    )

    assert base["pauses"]["count"] == 1
    assert rejected["pauses"]["count"] == 0
