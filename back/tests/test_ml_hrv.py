"""VFC sobre series RR conocidas."""

import numpy as np
import pytest

from app.ml import hrv


def _series(rr_ms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return rr_ms, np.cumsum(rr_ms) / 1000


def test_dominio_del_tiempo_con_valores_exactos() -> None:
    nn, times = _series(np.array([800.0, 860.0, 780.0, 900.0, 820.0]))

    result = hrv.time_domain(nn, times, np.diff(nn))

    assert result["sdnnMs"] == pytest.approx(np.std(nn, ddof=1))
    assert result["rmssdMs"] == pytest.approx(np.sqrt(np.mean(np.diff(nn) ** 2)))
    # |60|, |80|, |120|, |80| superan 50 ms: 100 %.
    assert result["pnn50Percent"] == pytest.approx(100.0)
    assert result["cv"] == pytest.approx(np.std(nn, ddof=1) / nn.mean())
    assert result["sdannMs"] is None  # menos de dos ventanas de 5 min


def test_sdann_es_el_desvio_de_las_medias_de_cinco_minutos() -> None:
    nn, times = _series(np.concatenate([np.full(374, 800.0), np.full(300, 1000.0)]))

    result = hrv.time_domain(nn, times, np.diff(nn))

    assert result["sdannMs"] == pytest.approx(np.std([800.0, 1000.0], ddof=1))


@pytest.mark.parametrize(("frequency", "band"), [(0.1, "lfMs2"), (0.25, "hfMs2")])
def test_la_modulacion_cae_en_su_banda(frequency: float, band: str) -> None:
    rr = np.full(4000, 800.0)
    times = np.cumsum(rr) / 1000
    rr = rr + 40 * np.sin(2 * np.pi * frequency * times)

    result = hrv.frequency_domain(rr, times)

    assert result is not None
    other = "hfMs2" if band == "lfMs2" else "lfMs2"
    assert result[band] > 10 * result[other]
    # Una senoide de amplitud 40 ms aporta A²/2 = 800 ms².
    assert result[band] == pytest.approx(800, rel=0.15)


def test_energia_es_la_suma_de_las_bandas_y_aparece_ulf_con_registro_largo() -> None:
    rng = np.random.default_rng(5)
    rr = 800 + 30 * rng.standard_normal(4000)
    times = np.cumsum(rr) / 1000
    rr = rr + 60 * np.sin(2 * np.pi * times / 1800)  # tendencia de 30 min

    result = hrv.frequency_domain(rr, times)

    assert result is not None
    assert result["ulfMs2"] is not None and result["ulfMs2"] > 0
    total = result["ulfMs2"] + result["vlfMs2"] + result["lfMs2"] + result["hfMs2"]
    assert result["totalPowerMs2"] == pytest.approx(total)
    assert len(result["spectrum"]["frequenciesHz"]) == len(result["spectrum"]["powerMs2PerHz"])


def test_sin_ventanas_completas_no_hay_espectro() -> None:
    nn, times = _series(np.full(100, 800.0))  # 80 s
    assert hrv.frequency_domain(nn, times) is None


def test_ulf_no_interpela_un_hueco_largo_del_registro() -> None:
    # Dos tramos con NN constantes y distinta FC, separados por 85 minutos.
    # La interpolación anterior creaba una tendencia no observada entre ambos.
    first = np.arange(0, 900, 1.0)
    second = np.arange(6000, 6900, 1.0)
    times = np.concatenate((first, second))
    nn = np.concatenate((np.full(first.size, 800.0), np.full(second.size, 1100.0)))

    result = hrv.frequency_domain(nn, times)

    assert result is not None
    assert result["windows"] == 6
    assert result["ulfMs2"] is None
    assert result["totalPowerMs2"] == pytest.approx(
        result["vlfMs2"] + result["lfMs2"] + result["hfMs2"]
    )
