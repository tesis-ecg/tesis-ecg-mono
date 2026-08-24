"""Un lote realista de 1 h, end-to-end.

Es el test que confirma que los números del diseño son medidos y no estimados:
~1,8 tramas/s, ~1,66 MB por hora comprimidos, ~7,2 MB descomprimidos. Si el
codec o el pipeline se degradan, acá se ve antes que en producción.

Marcado `slow` porque decodificar 1,8 M de muestras en Python puro lleva
decenas de segundos — exactamente la razón por la que el procesamiento no vive
dentro del request.
"""

import numpy as np
import pytest

from app.core.s3 import get_object
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.study import Study
from app.ml.decompression import FRAME_BYTES, SAMPLE_RATE_HZ
from app.modules.ingest.processing import process_batch
from tests.frame_builder import encode_samples, synth_samples
from tests.ingest_helpers import post_frames

ONE_HOUR_SAMPLES = SAMPLE_RATE_HZ * 3600  # 1.800.000


@pytest.mark.slow
async def test_one_hour_batch_end_to_end(client, s3, db, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    samples = synth_samples(ONE_HOUR_SAMPLES)
    frames = encode_samples(samples)

    compressed_bytes = len(frames) * FRAME_BYTES
    frames_per_second = len(frames) / 3600
    ratio = (ONE_HOUR_SAMPLES * 4) / compressed_bytes

    # Cotas de orden de magnitud, no de precisión. La referencia del firmware es
    # 12,8× medido sobre ruido ambulatorio real (DATAFLOW.md §9.1); esta señal
    # sintética da ~6× porque su línea de base es un diente de sierra de alta
    # frecuencia, que para un predictor de orden 2 es PEOR que un ECG de verdad.
    # Lo que fija el test es que nadie rompa el codec sin que se note.
    assert 0.5 < frames_per_second < 6, f"{frames_per_second:.2f} tramas/s"
    assert compressed_bytes < 4 * 1024 * 1024, f"{compressed_bytes / 1e6:.2f} MB"
    assert ratio > 5, f"ratio de compresión {ratio:.1f}×"

    response = await post_frames(client, device, api_key, frames)
    assert response.status_code == 202
    body = response.json()
    assert body["framesAccepted"] == len(frames)

    await process_batch(db, body["batchId"])

    batch = await db.get(ECGBatch, body["batchId"])
    study = await db.get(Study, body["studyId"])
    assert batch is not None and study is not None
    assert batch.processing_status == ProcessingStatus.DONE
    assert study.samples_count == ONE_HOUR_SAMPLES

    signal = np.frombuffer(get_object(study.ecg_segments[0]["key"]), dtype="<f4")
    assert signal.size == ONE_HOUR_SAMPLES
    assert signal.nbytes == ONE_HOUR_SAMPLES * 4  # ~7,2 MB descomprimidos

    # El visor pide como mucho 20.000 puntos: tiene que existir un nivel que
    # entre en ese presupuesto, si no la vista general no se puede dibujar.
    assert study.ecg_pyramid_levels
    assert any(level["pointCount"] <= 20_000 for level in study.ecg_pyramid_levels)


@pytest.mark.slow
async def test_a_full_hour_round_trips_without_losing_a_sample(s3) -> None:
    """1,8 M de muestras, codec ida y vuelta, comparación exacta."""
    from app.ml.decompression import decode_frame

    samples = synth_samples(ONE_HOUR_SAMPLES)
    decoded: list[int] = []
    for frame in encode_samples(samples):
        decoded.extend(decode_frame(frame).raw_uV[0].tolist())

    assert decoded == [s.raw_uV[0] for s in samples]
