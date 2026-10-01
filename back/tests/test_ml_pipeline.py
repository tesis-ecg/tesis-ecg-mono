"""El pipeline completo, sobre numpy y sin base de datos.

Es la prueba de que la estrategia del documento funciona de punta a punta:
`QRS normal → ruido → QRS anómalo`, sin un solo latido etiquetado.
"""

import numpy as np
import pytest

from app.core.config import settings
from app.db.models.ecg_event import ECGEventType
from app.db.models.signal_quality import SignalQualityLevel
from app.ml import pipeline
from app.ml.decompression import FLAG_ADC_SATURATED, FLAG_LEAD_OFF
from tests.ecg_synth import SAMPLE_RATE, synth_ecg


def _config(**overrides: object) -> pipeline.PipelineConfig:
    config = pipeline.build_config(settings, SAMPLE_RATE)
    return config if not overrides else _replace(config, **overrides)


def _replace(config: pipeline.PipelineConfig, **overrides: object) -> pipeline.PipelineConfig:
    from dataclasses import replace

    return replace(config, **overrides)  # type: ignore[arg-type]


def _kinds(result: pipeline.PipelineResult) -> set[str]:
    return {finding.kind for finding in result.findings}


def test_un_ecg_limpio_no_produce_ningun_hallazgo() -> None:
    """El caso que más importa: un Holter normal tiene que ser silencioso.

    Un motor que marca algo en cada registro sano es un motor que el médico
    aprende a ignorar en la primera semana.
    """
    signal = synth_ecg(duration_s=300.0)
    config = _config()
    result = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    assert result.findings == ()
    assert {interval.level for interval, _ in result.quality_intervals} == {SignalQualityLevel.GOOD}
    assert result.metrics["goodRatio"] == 1.0


def test_un_foco_ectopico_se_detecta_agrupado_y_con_su_carga() -> None:
    """La afirmación central del método: "esta morfología aparece N veces, el
    X % de tus latidos" — sin saber cómo se llama la enfermedad."""
    signal = synth_ecg(duration_s=1_200.0, ectopic_every=12)
    config = _config()
    result = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )

    headers = [f for f in result.findings if f.kind == "recurrent_morphology"]
    assert len(headers) == 1
    header = headers[0]
    assert header.scope == "study"
    assert header.beat_count == pytest.approx(len(signal.ectopic_peaks), rel=0.05)
    # ~8 % de carga: uno de cada doce latidos.
    assert header.metadata["burdenPct"] == pytest.approx(100 / 12, rel=0.15)
    # Un foco real es compacto; una bolsa de artefactos no lo sería.
    assert float(header.metadata["meanIntraCorrelation"]) > 0.95

    episodios = [f for f in result.findings if f.kind == "morphology_anomaly"]
    assert episodios
    assert all(f.event_type is ECGEventType.ANOMALY for f in episodios)
    assert all(f.cluster_id == header.cluster_id for f in episodios)


def test_el_ruido_no_se_confunde_con_una_anomalia() -> None:
    """La aserción falsable que sostiene toda la Etapa 1.

    Un artefacto de movimiento se parece muchísimo más a una arritmia que a un
    latido normal. Si el gate no separa ruido primero, ese tramo produciría
    decenas de "morfologías atípicas" que no son nada.
    """
    signal = synth_ecg(duration_s=600.0)
    ruidosa = signal.signal_mv.copy()
    rng = np.random.default_rng(11)
    inicio, fin = 300 * SAMPLE_RATE, 360 * SAMPLE_RATE
    ruidosa[inicio:fin] += rng.normal(0.0, 0.6, fin - inicio).astype(np.float32)

    config = _config()
    result = pipeline.analyze_batch(
        ruidosa,
        signal.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )

    # El tramo se marca como no analizable...
    malos = [
        interval
        for interval, _ in result.quality_intervals
        if interval.level is SignalQualityLevel.BAD
    ]
    assert malos, "el gate no detectó el tramo ruidoso"
    assert any(inicio <= interval.start_sample < fin for interval in malos)

    # ...y CERO anomalías de morfología ahí adentro.
    anomalias = [
        f
        for f in result.findings
        if f.kind == "morphology_anomaly" and inicio <= f.start_sample < fin
    ]
    assert anomalias == []


def test_el_electrodo_despegado_lo_atrapa_la_capa_a_y_no_los_indices_espectrales() -> None:
    """Las capas no son redundantes. Medido: `zhao2018` clasifica un flatline
    como `Barely acceptable` — falla en el modo de falla más obvio del electrodo
    seco. Los bits del AFE no fallan ahí."""
    signal = synth_ecg(duration_s=120.0)
    señal = signal.signal_mv.copy()
    flags = signal.flags.copy()
    señal[60 * SAMPLE_RATE : 90 * SAMPLE_RATE] = 0.0
    flags[60 * SAMPLE_RATE : 90 * SAMPLE_RATE] |= FLAG_LEAD_OFF

    config = _config()
    result = pipeline.analyze_batch(
        señal,
        flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    razones = {
        interval.reason
        for interval, _ in result.quality_intervals
        if interval.level is SignalQualityLevel.BAD
    }
    assert razones == {"lead_off"}
    # Y no se emite `flatline` encima: el `lead_off` de la Capa A ya cubre el
    # tramo, y dos bandas sobre la misma zona no le dicen nada nuevo al médico.
    assert "flatline" not in _kinds(result)


def test_las_coordenadas_salen_absolutas_al_estudio() -> None:
    """El pipeline traslada una sola vez, al final: ningún consumidor tiene que
    acordarse de sumar el offset del lote."""
    signal = synth_ecg(duration_s=600.0, ectopic_every=10)
    offset = 1_800_000
    config = _config()
    result = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=offset,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    assert result.findings
    assert all(finding.start_sample >= offset for finding in result.findings)
    assert all(interval.start_sample >= offset for interval, _ in result.quality_intervals)


def test_el_banco_acumula_entre_lotes_sin_releer_nada() -> None:
    """Un foco de 17 latidos por hora llega a 412 miembros al final del estudio
    sin volver a tocar un byte de la historia."""
    config = _config()
    bank = pipeline.empty_bank(config)
    conteos = []
    for indice in range(3):
        signal = synth_ecg(duration_s=600.0, ectopic_every=12, seed=indice + 1)
        result = pipeline.analyze_batch(
            signal.signal_mv,
            signal.flags,
            start_sample_index=indice * 300_000,
            bank=bank,
            config=config,
            batch_id=f"b{indice}",
        )
        bank = result.bank
        conteos.append(bank.beats_seen)

    assert conteos == sorted(conteos)
    assert conteos[0] < conteos[-1]
    header = next(f for f in result.findings if f.kind == "recurrent_morphology")
    assert header.beat_count is not None and header.beat_count > 100


def test_un_lote_ya_plegado_no_vuelve_a_sumarse_al_banco() -> None:
    config = _config()
    signal = synth_ecg(duration_s=300.0, ectopic_every=12)
    primero = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    segundo = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=primero.bank,
        config=config,
        batch_id="b1",
    )
    assert segundo.bank.beats_seen == primero.bank.beats_seen
    assert {f.dedupe_key for f in segundo.findings} == {f.dedupe_key for f in primero.findings}


def test_un_estudio_sin_senal_analizable_no_hace_explotar_el_motor() -> None:
    """Chaleco mal puesto todo el registro: no hay plantilla posible. Tiene que
    devolver solo hallazgos de calidad, no fallar."""
    n = 300 * SAMPLE_RATE
    config = _config()
    result = pipeline.analyze_batch(
        np.zeros(n, dtype=np.float32),
        np.full(n, FLAG_ADC_SATURATED, dtype=np.uint8),
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    assert result.bank.templates == ()
    assert result.metrics["goodRatio"] == 0.0
    assert "morphology_anomaly" not in _kinds(result)


def test_un_lote_mas_corto_que_un_latido_no_rompe_nada() -> None:
    config = _config()
    result = pipeline.analyze_batch(
        np.zeros(50, dtype=np.float32),
        np.zeros(50, dtype=np.uint8),
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    assert result.metrics["analyzedBeats"] == 0.0


def test_una_pausa_real_se_detecta_y_avisa_al_paciente() -> None:
    signal = synth_ecg(duration_s=120.0)
    señal = signal.signal_mv.copy()
    # Se borra un latido entero: el R-R pasa a valer el doble.
    inicio = 60 * SAMPLE_RATE
    señal[inicio : inicio + int(2.6 * SAMPLE_RATE)] = signal.signal_mv[:1].repeat(
        int(2.6 * SAMPLE_RATE)
    )
    flags = signal.flags.copy()
    flags[inicio : inicio + int(2.6 * SAMPLE_RATE)] = 0

    config = _config()
    result = pipeline.analyze_batch(
        señal,
        flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    pausas = [f for f in result.findings if f.kind == "pause"]
    assert pausas, "no se detectó la pausa"
    assert pausas[0].alert_message is not None
    assert pausas[0].event_type is ECGEventType.PAUSE


def test_sin_compensar_el_retardo_del_firmware_el_motor_entero_enmudece() -> None:
    """La regresión que dejó al motor mudo sobre señal real del chaleco.

    El `FLAG_R_PEAK` llega 250 ms después del pico y la tolerancia del bSQI es
    de ±150 ms: sin compensar, los dos detectores no coinciden en ningún latido,
    el bSQI da 0, **todas** las ventanas caen a `marginal` y la máscara de
    analizable queda vacía. Como la Etapa 2 y las reglas de ritmo solo miran
    señal `good`, el lote sale con cero hallazgos y cero errores: la falla es
    silenciosa, que es lo que la hace peligrosa.
    """
    from dataclasses import replace

    signal = synth_ecg(duration_s=120.0, ectopic_every=8)
    config = _config()

    sin_compensar = _replace(config, quality=replace(config.quality, firmware_lag_samples=0))
    mudo = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(sin_compensar),
        config=sin_compensar,
        batch_id="b1",
    )
    assert mudo.metrics["goodRatio"] == 0.0
    assert {interval.reason for interval, _ in mudo.quality_intervals} == {"bsqi"}
    # Lo que de verdad duele: la Etapa 2 no ve un solo latido y la serie R-R
    # queda vacía, así que tampoco puede haber bradicardia, taquicardia ni pausa
    # — las tres únicas que notifican al paciente.
    assert mudo.metrics["analyzedBeats"] == 0.0
    assert mudo.metrics["beatsSeen"] == 0.0
    assert mudo.findings == ()

    # Con la compensación puesta, la misma señal se analiza entera.
    vivo = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        batch_id="b1",
    )
    assert vivo.metrics["goodRatio"] == 1.0
    assert vivo.metrics["medianBsqi"] == 1.0
    assert vivo.metrics["analyzedBeats"] > 100
