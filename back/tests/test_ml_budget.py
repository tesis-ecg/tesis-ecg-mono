"""Presupuesto de cómputo del motor sobre un lote realista de 1 h.

El motor corre en un hilo (`app/core/workers.py`) justamente porque cuesta CPU.
Este test fija cuánto: si alguien introduce un bucle Python sobre las 1,8 M de
muestras, acá se ve antes que en producción — donde se traduce en un lote que
tarda minutos y en un paciente que no ve su ECG.

Marcado `slow`: genera y analiza una hora entera de ECG con morfología real.
"""

import time

import pytest

from app.core.config import settings
from app.ml import pipeline
from app.ml.morphology import bank_to_state
from tests.ecg_synth import synth_ecg

#: Medido en este proyecto: ~0,3 s por hora de señal. El techo está puesto un
#: orden de magnitud por encima para que el test falle ante una regresión
#: estructural y no ante el ruido de una máquina cargada.
BUDGET_SECONDS = 15.0


@pytest.mark.slow
def test_una_hora_de_ecg_entra_en_el_presupuesto_de_computo() -> None:
    signal = synth_ecg(duration_s=3600.0, ectopic_every=12)
    config = pipeline.build_config(settings, 500)
    bank = pipeline.empty_bank(config)

    # Una pasada corta primero: el costo del import de neurokit2/scipy lo paga el
    # `warmup_ml` del lifespan en producción, no el lote.
    pipeline.analyze_batch(
        signal.signal_mv[:5_000],
        signal.flags[:5_000],
        start_sample_index=0,
        bank=bank,
        config=config,
        fold_key="warmup",
    )

    started = time.perf_counter()
    result = pipeline.analyze_batch(
        signal.signal_mv,
        signal.flags,
        start_sample_index=0,
        bank=bank,
        config=config,
        fold_key="una-hora",
    )
    elapsed = time.perf_counter() - started

    assert elapsed < BUDGET_SECONDS, f"{elapsed:.2f} s para una hora de señal"
    # Y que el resultado sea el correcto, no solo rápido.
    assert result.metrics["analyzedBeats"] > 3_000
    assert any(finding.kind == "recurrent_morphology" for finding in result.findings)
    # El tope de revisión acota la salida sin importar cuánta señal entre.
    episodes = [f for f in result.findings if f.kind == "morphology_anomaly"]
    assert len(episodes) <= settings.ml_findings_max_per_kind


@pytest.mark.slow
def test_el_banco_de_plantillas_no_crece_con_la_duracion_del_estudio() -> None:
    """Lo que hace que 24 h entren en memoria: el estado es O(#morfologías).

    Con la matriz de latidos completa serían ~100 MB por estudio y un DBSCAN que
    muere por OOM (medido). Con el banco son ~40 KB, sin importar cuántas horas
    pasen.
    """
    config = pipeline.build_config(settings, 500)
    bank = pipeline.empty_bank(config)
    tamaños = []
    for hora in range(6):
        signal = synth_ecg(duration_s=600.0, ectopic_every=12, seed=hora + 1)
        result = pipeline.analyze_batch(
            signal.signal_mv,
            signal.flags,
            start_sample_index=hora * 300_000,
            bank=bank,
            config=config,
            fold_key=f"b{hora}",
        )
        bank = result.bank
        tamaños.append(len(bank.templates))

    assert bank.beats_seen > 3_000
    assert max(tamaños) <= settings.ml_template_max
    _, blob = bank_to_state(bank)
    assert len(blob) < 100_000, f"{len(blob)} bytes de centroides"
