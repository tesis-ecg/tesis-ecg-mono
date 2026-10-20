"""El formato persistido de las etiquetas por latido (`app/ml/beat_labels.py`)."""

import numpy as np
import pytest

from app.ml import beat_labels as bl


def _records() -> bl.BeatLabelRecords:
    return bl.build_records(
        np.array([1000, 1500, 2100], dtype=np.int64),
        np.array([bl.CODE_N, bl.CODE_V, bl.CODE_X], dtype=np.uint8),
        np.array([0, bl.EVIDENCE_WIDE, 0], dtype=np.uint8),
        np.array([-25, -60, 0], dtype=np.int64),
        np.array([30, 80, 0], dtype=np.int64),
        np.array([-90, 0, 0], dtype=np.int64),
        np.array([1.2, 1.6, np.nan], dtype=np.float32),
        qrs_measured=np.array([True, True, False]),
        p_detected=np.array([True, False, False]),
    )


def test_un_registro_pesa_20_bytes_y_el_formato_no_cambia_sin_querer() -> None:
    """20 B por latido: 15 días a 70 lpm son ~30 MB. Cambiar el dtype invalida
    todo chunk ya escrito, así que el tamaño y los nombres quedan fijados acá."""
    assert bl.BEAT_LABEL_DTYPE.itemsize == 20
    assert bl.BEAT_LABEL_DTYPE.names == (
        "sample_index",
        "label",
        "evidence",
        "qrs_onset",
        "qrs_offset",
        "p_onset",
        "qrs_amplitude_mv",
    )
    assert bl.BEAT_LABEL_FORMAT == 1


def test_codificar_y_decodificar_es_exacto() -> None:
    records = _records()

    decoded = bl.decode_beat_labels(bl.encode_beat_labels(records))

    assert decoded.tobytes() == records.tobytes()
    assert [chr(code) for code in decoded["label"]] == ["N", "V", "X"]


def test_lo_que_no_se_midio_queda_marcado_y_nunca_se_inventa() -> None:
    records = _records()

    assert records["qrs_onset"][2] == bl.NOT_MEASURED
    assert records["p_onset"][1] == bl.NOT_MEASURED
    assert np.isnan(records["qrs_amplitude_mv"][2])
    # Los milisegundos se derivan: 55 ms de QRS y 65 ms de PR a 500 Hz.
    assert bl.qrs_ms(records, 500)[0] == pytest.approx(110.0)
    assert bl.pr_ms(records, 500)[0] == pytest.approx(130.0)
    assert np.isnan(bl.pr_ms(records, 500)[1])
    assert np.isnan(bl.qrs_ms(records, 500)[2])
    # La evidencia suma lo medido a lo que ya traía.
    assert records["evidence"][0] & bl.EVIDENCE_QRS_MEASURED
    assert records["evidence"][0] & bl.EVIDENCE_P_DETECTED
    assert records["evidence"][1] & bl.EVIDENCE_WIDE
    assert not records["evidence"][1] & bl.EVIDENCE_P_DETECTED


def test_un_formato_desconocido_o_un_largo_roto_se_rechazan() -> None:
    payload = bl.encode_beat_labels(_records())

    with pytest.raises(ValueError, match="formato"):
        bl.decode_beat_labels(payload, format=99)
    with pytest.raises(ValueError, match="tamaño"):
        bl.decode_beat_labels(payload[:-1])
    assert bl.decode_beat_labels(b"").size == 0
