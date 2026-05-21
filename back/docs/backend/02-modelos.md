# Backend — Modelos de datos

## Base común

Todos los modelos heredan de `TimestampMixin` definido en `app/db/base.py`:

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | Generado con `uuid4()` |
| `created_at` | DateTime | Set automático al crear |
| `updated_at` | DateTime | Set automático al modificar |
| `deleted_at` | DateTime (nullable) | Soft delete — `NULL` = activo |

Los repositorios filtran `WHERE deleted_at IS NULL` por defecto.

---

## Doctor

```
doctor
  id              UUID PK
  auth0_id        String  UNIQUE   -- sub del token Auth0
  email           String  UNIQUE NOT NULL
  name            String  NOT NULL
  surname         String  NOT NULL
  specialty       String  NULLABLE
  license_number  String  NULLABLE
  is_active       Boolean DEFAULT true
  deleted_at      DateTime NULLABLE
```

---

## Patient

```
patient
  id                  UUID PK
  doctor_id           UUID FK → doctor.id  NOT NULL
  medical_record_num  String  UNIQUE NOT NULL  -- número de historia clínica
  first_name          String  NOT NULL
  last_name           String  NOT NULL
  date_of_birth       Date    NULLABLE
  dni                 String  NULLABLE
  phone               String  NULLABLE
  email               String  NULLABLE
  deleted_at          DateTime NULLABLE
```

Control de acceso: un médico solo puede ver/modificar sus propios pacientes. Se verifica en la capa de servicio (`patient_service.py`) comparando `patient.doctor_id == current_doctor.id`.

---

## Device

```
device
  id                  UUID PK
  serial_number       String  UNIQUE NOT NULL
  patient_id          UUID FK → patient.id  NULLABLE  -- puede estar sin asignar
  api_key_hash        String  NOT NULL  -- hash bcrypt de la API key
  firmware_version    String  NULLABLE
  last_seen           DateTime NULLABLE
  last_battery_pct    Integer NULLABLE
  last_sd_free_mb     Integer NULLABLE
  is_active           Boolean DEFAULT true
  deleted_at          DateTime NULLABLE
```

La API key se genera con `secrets.token_urlsafe(32)`, se hashea con bcrypt y se guarda solo el hash. La key en texto plano se retorna **una sola vez** al provisionar el dispositivo.

---

## ECGBatch

Una fila por cada upload horario del dispositivo.

```
ecg_batch
  id                  UUID PK
  device_id           UUID FK → device.id  NOT NULL
  received_at         DateTime NOT NULL  -- timestamp del servidor
  batch_timestamp     Integer  NOT NULL  -- Unix timestamp del dispositivo
  duration_seconds    Integer  NOT NULL
  sample_rate         Integer  NOT NULL  -- Hz, ej: 250
  num_channels        Integer  NOT NULL DEFAULT 3
  num_samples         Integer  NOT NULL
  compression_type    String   NOT NULL  -- ej: "delta"
  s3_key              String   NOT NULL  -- clave en S3/MinIO
  file_size_bytes     Integer  NULLABLE
  processing_status   Enum     DEFAULT "PENDING"
    -- valores: PENDING | PROCESSING | DONE | FAILED
  processing_error    String   NULLABLE
  firmware_version    String   NULLABLE
  deleted_at          DateTime NULLABLE
```

**Patrón de S3 key**: `ecg/{device_id}/{YYYY-MM-DD}/{batch_timestamp}.bin`

---

## ECGEvent

Evento cardíaco detectado por el pipeline ML dentro de un batch.

```
ecg_event
  id                      UUID PK
  batch_id                UUID FK → ecg_batch.id NOT NULL
  event_type              Enum
    -- TACHYCARDIA | BRADYCARDIA | AFIB | PVC | PAUSE | NOISE | OTHER
  severity                Enum
    -- LOW | MEDIUM | HIGH | CRITICAL
  timestamp_in_recording  Float NOT NULL  -- segundos desde inicio del batch
  duration_seconds        Float NULLABLE
  confidence_score        Float NULLABLE  -- 0.0–1.0
  metadata                JSONB NULLABLE  -- detalles del algoritmo: HR, RR intervals, etc.
  deleted_at              DateTime NULLABLE
```

---

## Alert

Notificación accionable para el médico, generada automáticamente para eventos HIGH/CRITICAL.

```
alert
  id              UUID PK
  patient_id      UUID FK → patient.id NOT NULL
  event_id        UUID FK → ecg_event.id NOT NULL
  severity        Enum  -- LOW | MEDIUM | HIGH | CRITICAL
  message         String NOT NULL
  created_at      DateTime NOT NULL
  seen_at         DateTime NULLABLE
  acknowledged_at DateTime NULLABLE
  acknowledged_by UUID FK → doctor.id NULLABLE
  deleted_at      DateTime NULLABLE
```

---

## Índices críticos (migration 001)

```sql
-- Timeline ECG por paciente
CREATE INDEX idx_ecg_batch_device_time ON ecg_batch(device_id, received_at DESC);

-- Batches pendientes de procesar
CREATE INDEX idx_ecg_batch_status ON ecg_batch(processing_status);

-- Alertas sin confirmar por paciente
CREATE INDEX idx_alert_patient_ack ON alert(patient_id, acknowledged_at);

-- Eventos por batch
CREATE INDEX idx_ecg_event_batch ON ecg_event(batch_id);
```

---

## Orden de creación en Alembic (migration 001)

Por dependencias de FK:
1. `doctor`
2. `patient` (FK → doctor)
3. `device` (FK → patient)
4. `ecg_batch` (FK → device)
5. `ecg_event` (FK → ecg_batch)
6. `alert` (FK → patient, ecg_event, doctor)
