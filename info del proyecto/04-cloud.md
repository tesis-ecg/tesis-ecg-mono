# Capa 3 — Cloud y Dashboard Médico

## Stack recomendado

| Componente | Tecnología | Justificación |
|---|---|---|
| Backend | Python FastAPI | Simple, tipado, async, buena documentación |
| Base de datos | PostgreSQL | Robusto, escalable, gratuito |
| Storage | S3-compatible | Archivos raw de ECG para descarga/análisis posterior |
| Auth | JWT con refresh tokens | Estándar de la industria |
| Hosting MVP | Railway, Render, o Firebase | Rápido de deployar, bajo costo inicial |
| Hosting producción | AWS (ECS + RDS + S3) o GCP | Escalable, compliant |

## Dashboard médico (web)

- React o Next.js
- Vista por paciente: timeline de ECG, FC, eventos
- Alertas configurables por el médico (umbrales de FC, arritmias)
- Descarga de registros en formato estándar (EDF, CSV)

## Modelo de datos

```
Doctor
  └── patients: [Patient]

Patient
  ├── device_id: string
  ├── sessions: [RecordingSession]
  └── alerts: [Alert]

RecordingSession
  ├── start_time, end_time
  ├── chunks: [ECGChunk]
  └── summary_metrics: {avg_hr, min_hr, max_hr, hrv}

ECGChunk
  ├── timestamp, duration_sec
  ├── sample_rate, num_samples
  ├── data_url: string (S3)
  └── metrics: {avg_hr, quality_score}

Alert
  ├── timestamp, type, severity
  ├── description
  └── acknowledged: boolean
```

## Recepción directa desde Holter

El Holter envía datos directamente al backend vía módulo SIM (LTE-M) cada hora, sin intermediarios. Ver [documentación completa del canal SIM](07-sim-celular.md).

### Endpoint de batch upload

```python
@app.post("/devices/{device_id}/ecg-batch")
async def receive_ecg_batch(device_id: str, request: Request, batch: ECGBatchPayload):
    """
    Recibe un batch de datos ECG directamente desde el Holter (via SIM).
    Autenticación por API key del dispositivo (no JWT).
    """
    # Verificar API key del dispositivo
    api_key = request.headers.get("X-API-Key")
    device = db.get_device_by_id_and_key(device_id, api_key)
    if not device:
        raise HTTPException(401, "Device not authenticated")
    
    # Procesar cada chunk del batch
    for chunk in batch.batch:
        # Decodificar base64 y descomprimir
        raw_data = decompress_delta(base64.b64decode(chunk.data_b64))
        
        # Subir a S3
        s3_url = await upload_to_s3(device_id, chunk.timestamp, raw_data)
        
        # Guardar metadata en PostgreSQL
        db.save_ecg_chunk(
            device_id=device_id,
            timestamp=chunk.timestamp,
            duration_sec=chunk.duration_sec,
            sample_rate=chunk.sample_rate,
            num_samples=chunk.num_samples,
            data_url=s3_url,
            source="sim"  # distinguir de datos que llegan via app
        )
    
    # Actualizar estado del dispositivo
    db.update_device_status(
        device_id=device_id,
        battery_pct=batch.battery_pct,
        sd_free_mb=batch.sd_free_mb,
        last_seen=datetime.utcnow()
    )
    
    return {"status": "ok", "chunks_received": len(batch.batch)}
```

### Modelo del payload

```python
class ECGChunkData(BaseModel):
    timestamp: int          # Unix timestamp del inicio del chunk
    duration_sec: int       # Duración en segundos (ej: 3600 para 1 hora)
    sample_rate: int        # Hz (250)
    num_samples: int        # Cantidad de muestras
    compression: str        # "delta" o "raw"
    data_b64: str           # Datos comprimidos en base64

class ECGBatchPayload(BaseModel):
    device_id: str
    firmware_version: str
    battery_pct: int
    sd_free_mb: int
    batch: list[ECGChunkData]
```

### Autenticación por API key

El Holter se autentica con una **API key única por dispositivo** (no JWT — el dispositivo opera sin interacción humana):

- Se genera durante el provisioning/manufactura y se graba en flash del XIAO Nordic
- Se envía en el header `X-API-Key` de cada request
- El backend valida contra la tabla `Device` en PostgreSQL
- No requiere flujo de login/refresh — el dispositivo opera sin interacción humana

### Campo `source` en ECGChunk

Se agrega un campo `source` al modelo `ECGChunk` para trazabilidad:

```
ECGChunk
  ├── ...campos existentes...
  └── source: "sim"    # Todos los datos llegan vía SIM batch
```
