# Backend — Endpoints de la API

## Autenticación por ruta

> **Estado actual: sin autenticación.** Todos los endpoints son abiertos.
> El plan de auth (API Key para dispositivo, Auth0 JWT para médicos) está en `04-autenticacion.md`.

| Grupo | Auth planificada |
|---|---|
| Dispositivo (`/devices/...`) | `X-API-Key` header (futuro) |
| Dashboard médico (`/doctors/`, `/patients/`, `/ecg-batches/`, `/alerts/`) | Auth0 JWT — rol `DOCTOR` o `ADMIN` (futuro) |
| Admin (`/admin/...`) | Auth0 JWT — rol `ADMIN` (futuro) |
| Health (`/health`) | Público |

---

## Dispositivo → Backend

### `POST /devices/{device_id}/ecg-batch`

Recibe el upload horario del Holter. El dispositivo tiene timeout de 30 s, por lo que la respuesta debe ser inmediata.

**Flujo**:
1. Valida API key → obtiene registro `Device`
2. Sube binario raw a S3
3. Inserta fila `ECGBatch` con `processing_status=PENDING`
4. Actualiza `Device.last_seen`, `last_battery_pct`, `last_sd_free_mb`
5. Encola tarea ML en background (`BackgroundTasks`)
6. Retorna `{batch_id, status: "ok", chunks_received: 1}`

**Body** (JSON + binario comprimido en base64):
```json
{
  "batch_timestamp": 1748000000,
  "duration_seconds": 3600,
  "sample_rate": 250,
  "num_channels": 3,
  "num_samples": 900000,
  "compression_type": "delta",
  "battery_pct": 78,
  "sd_free_mb": 121,
  "firmware_version": "1.0.3",
  "data": "<base64-encoded binary>"
}
```

---

## Dashboard médico — Médico

### `GET /doctors/me`
Perfil del médico autenticado.

### `PUT /doctors/me`
Actualizar datos del perfil (especialidad, matrícula).

### `GET /doctors/me/patients`
Lista paginada de pacientes asignados al médico. Params: `page`, `per_page`, `search` (nombre/DNI).

### `GET /doctors/me/alerts`
Todas las alertas sin confirmar de los pacientes del médico. Ordenado por severidad + fecha.

---

## Dashboard médico — Pacientes

### `POST /patients`
Crear nuevo paciente y asignarlo al médico autenticado.

### `GET /patients/{patient_id}`
Detalle de paciente. Sólo accesible si `patient.doctor_id == current_doctor.id`.

### `PUT /patients/{patient_id}`
Actualizar datos demográficos.

### `DELETE /patients/{patient_id}`
Soft delete del paciente.

### `GET /patients/{patient_id}/ecg-batches`
Lista paginada de batches ECG del paciente (más reciente primero). Params: `page`, `per_page`, `from_date`, `to_date`.

### `GET /patients/{patient_id}/alerts`
Historial de alertas del paciente. Params: `page`, `per_page`, `acknowledged` (bool filter).

### `GET /patients/{patient_id}/ecg-summary`
Estadísticas agregadas: `avg_hr`, `min_hr`, `max_hr`, `sdnn`, `rmssd`, conteo de eventos por tipo, último batch procesado.

---

## Dashboard médico — Batches ECG

### `GET /ecg-batches/{batch_id}`
Metadatos del batch + lista de `ECGEvent` detectados.

### `GET /ecg-batches/{batch_id}/download-url`
Genera URL presignada de S3 (TTL 15 min) para descargar el archivo raw.

### `GET /ecg-batches/{batch_id}/events`
Lista paginada de eventos ECG dentro del batch.

---

## Dashboard médico — Alertas

### `GET /alerts`
Todas las alertas de los pacientes del médico. Params: `page`, `per_page`, `severity`, `acknowledged`.

### `PUT /alerts/{alert_id}/acknowledge`
Marca la alerta como confirmada. Registra `acknowledged_at` y `acknowledged_by`.

### `PUT /alerts/{alert_id}/seen`
Marca la alerta como vista (se abrió en el dashboard).

---

## Admin

### `POST /admin/devices`
Provisiona un nuevo dispositivo. Genera `api_key` con `secrets.token_urlsafe(32)`, guarda el hash. **Retorna la key en texto plano una sola vez.**

Response:
```json
{
  "device_id": "...",
  "serial_number": "HOLTER-001",
  "api_key": "...(retornado una sola vez)"
}
```

### `GET /admin/devices`
Lista todos los dispositivos con estado de asignación.

### `PUT /admin/devices/{device_id}/assign`
Asigna un dispositivo a un paciente.

### `DELETE /admin/devices/{device_id}`
Soft delete del dispositivo.

### `POST /admin/doctors`
Crea cuenta de médico (crea usuario en Auth0 vía Management API + fila en tabla `doctor`).

---

## Sistema

### `GET /health`
Siempre público. Retorna `{"status": "ok"}`.

### `GET /docs`
Swagger UI (incluido por FastAPI automáticamente en dev).
