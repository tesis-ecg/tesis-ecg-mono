# Backend — Pipeline de análisis ECG (ML)

## Por qué Python

La elección de Python como lenguaje del backend se justifica principalmente por el ecosistema de ML para señales biomédicas: `numpy`, `scipy`, `neurokit2`. En Java/Spring esto requeriría JNI o microservicios separados.

## Arquitectura: BackgroundTasks (MVP)

FastAPI incluye `BackgroundTasks`: permite encolar una función para ejecutar **después** de retornar la respuesta HTTP, en el mismo proceso. Esto es correcto para el MVP porque:

- El dispositivo tolera un delay de hasta 1h en que los eventos aparezcan en el dashboard (documentado en `info del proyecto/04-cloud.md`).
- No requiere Redis, Celery, ni workers adicionales.
- El procesamiento de un batch (neurokit2 sobre ~900k muestras) toma ~3-10s de CPU — aceptable en un proceso compartido con ~1 dispositivo activo.

**Path de upgrade**: cuando haya 10+ dispositivos simultáneos, reemplazar la línea de encolado por `celery_app.send_task(...)` sin tocar `pipeline.py`.

```python
# En routers/devices.py — el cambio es UNA línea:
# MVP:
background_tasks.add_task(asyncio.to_thread, process_batch, batch_id=batch.id)
# Producción:
celery_app.send_task("ml.process_batch", args=[str(batch.id)])
```

## Flujo del pipeline (`app/ml/pipeline.py`)

```
process_batch(batch_id: UUID) -> None
  │
  ├── 1. Cargar ECGBatch de DB (s3_key, compression_type, sample_rate, num_channels)
  │       Actualizar processing_status = "PROCESSING"
  │
  ├── 2. Descargar archivo raw de S3 (boto3)
  │
  ├── 3. Descomprimir → numpy array shape (num_samples, num_channels)
  │       ver app/ml/decompression.py
  │
  ├── 4. Por cada canal:
  │   ├── neurokit2.ecg_clean(signal, sampling_rate)
  │   ├── neurokit2.ecg_peaks(cleaned, sampling_rate) → R-peaks array
  │   └── neurokit2.hrv_time(peaks, sampling_rate) → {SDNN, RMSSD, mean_HR, ...}
  │
  ├── 5. Clasificar arritmias (reglas, MVP):
  │   ├── Taquicardia: mean HR > 100 lpm
  │   ├── Bradicardia: mean HR < 60 lpm
  │   ├── Pausa: intervalo RR > 2.0 s
  │   └── PVC frecuentes: outliers morfológicos vía scipy
  │
  ├── 6. Persistir ECGEvent rows (bulk insert)
  │
  ├── 7. Para eventos HIGH/CRITICAL → crear Alert
  │
  └── 8. Actualizar ECGBatch.processing_status = "DONE"
      (en except: processing_status = "FAILED", guardar error en processing_error)
```

## Módulos

### `app/ml/decompression.py`

Decodifica el payload del dispositivo:
1. Base64 decode → bytes
2. Delta decode: reconstruir la señal desde deltas (cada muestra = suma acumulativa de deltas)
3. Reshape a `(num_samples, num_channels)` numpy array
4. Convertir a mV según factor de escala del AFE (a definir con equipo de Biomédica en Fase 1)

```python
def decode_batch(data_b64: str, num_channels: int) -> np.ndarray:
    raw = base64.b64decode(data_b64)
    deltas = np.frombuffer(raw, dtype=np.int16)
    signal = np.cumsum(deltas.reshape(-1, num_channels), axis=0).astype(np.float32)
    return signal  # shape: (num_samples, num_channels)
```

### `app/ml/rpeak_detection.py`

```python
import neurokit2 as nk

def detect_rpeaks(signal: np.ndarray, sample_rate: int) -> dict:
    cleaned = nk.ecg_clean(signal, sampling_rate=sample_rate)
    _, info = nk.ecg_peaks(cleaned, sampling_rate=sample_rate)
    return info  # info["ECG_R_Peaks"] → índices de R-peaks
```

### `app/ml/hrv.py`

```python
def compute_hrv(r_peaks: np.ndarray, sample_rate: int) -> dict:
    hrv = nk.hrv_time(r_peaks, sampling_rate=sample_rate)
    return {
        "sdnn": float(hrv["HRV_SDNN"].iloc[0]),
        "rmssd": float(hrv["HRV_RMSSD"].iloc[0]),
        "mean_hr": float(hrv["HRV_MeanNN"].iloc[0]),  # en ms → convertir a bpm
        "min_hr": ...,
        "max_hr": ...,
    }
```

### `app/ml/arrhythmia.py`

Clasificador basado en reglas para el MVP. No requiere modelo entrenado.

| Evento | Regla |
|---|---|
| `TACHYCARDIA` | mean HR > 100 lpm durante ≥ 30s |
| `BRADYCARDIA` | mean HR < 60 lpm durante ≥ 30s |
| `PAUSE` | intervalo RR > 2.0 s |
| `PVC` | outlier morfológico: amplitud ±2.5 SD del promedio de la señal |

Cada evento detectado se convierte en un `ECGEvent` con:
- `timestamp_in_recording`: offset en segundos desde el inicio del batch
- `severity`: `LOW` para bradicardia/taquicardia leve, `HIGH/CRITICAL` para pausa o arritmia sostenida
- `confidence_score`: null en reglas, 0.0–1.0 cuando haya modelos ML

## Escalado futuro

El diseño de `pipeline.py` permite reemplazar el clasificador por un modelo ONNX o TensorFlow Lite sin cambiar la interfaz:

```python
# MVP:
events = arrhythmia.classify_rules(r_peaks, hrv_metrics)

# Futuro con modelo:
events = arrhythmia.classify_model(signal, model=load_onnx("ecg_classifier.onnx"))
```
