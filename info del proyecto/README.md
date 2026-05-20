# Arquitectura de Comunicación: Holter Wearable ↔ Cloud

Arquitectura de 2 capas para un wearable ECG modular (chaleco/top) con AFE + XIAO Nordic, orientado a monitoreo cardíaco preventivo continuo (semanas/meses) para pacientes de 40-70 años en Argentina.

## Decisión principal

**Módulo celular SIM (LTE-M) como único canal de comunicación, arquitectura standalone.** El dispositivo envía datos ECG directamente al backend cada hora vía celular, sin BLE, sin app, sin WiFi del paciente. El módulo concreto (SIM7080G como candidato técnico) se confirma en Fase 1. Ver [justificación completa](01-justificacion.md).

## Diagrama general

```
┌──────────────────────────────────────┐
│         HOLTER                        │
│         (XIAO Nordic + AFE)           │
│                                        │
│      ECG ADC (3 canales)              │
│              ↓                         │
│          SD Card (128 MB)              │
│       (buffer primario,               │
│        always-on)                     │
│              ↓ cada 1h                 │
│         Módulo SIM                    │
│        (LTE-M)                        │
└──────────────┬─────────────────────────┘
               │ HTTPS / LTE-M (TLS 1.2)
               ▼
┌──────────────────────────┐
│    CLOUD                  │
│                            │
│  API REST (FastAPI)        │
│  PostgreSQL + S3           │
│  Dashboard Médico (Web)    │
└────────────────────────────┘
```

## Flujo de datos por escenario

```
Estado normal:      Holter → SD (buffer) → SIM --LTE-M--> Cloud (batch cada 1h)
Sin señal celular:  Holter → SD acumula (hasta ~2 días con SD de 128 MB). Al recuperar señal: envía pendientes
Fallo de envío:     Datos permanecen en SD. Reintento en el próximo ciclo. Backoff exponencial.
```

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| MCU | XIAO Nordic (Seeed Studio XIAO nRF52840) |
| Almacenamiento | microSD 128 MB via SPI (~2 días de buffer sin señal) |
| Batería | Li-Po 500-800 mAh (2-3 días) |
| Módulo celular | SIM via UART (LTE-M/NB-IoT — SIM7080G candidato técnico) |
| Backend | Python FastAPI |
| DB | PostgreSQL |
| Storage | S3 (AWS) o GCS |
| Dashboard | React / Next.js |
| Hosting (MVP) | Railway o Render |

## Documentos

| # | Documento | Contenido |
|---|---|---|
| 1 | [Justificación — Arquitectura SIM](01-justificacion.md) | Comparación de opciones, factores decisivos para elegir SIM standalone |
| 2 | [Firmware del Holter](02-firmware-holter.md) | Flujo SIM principal, SD, máquina de estados |
| 3 | [Cloud y Dashboard](04-cloud.md) | FastAPI, modelo de datos, dashboard médico |
| 4 | [Batería y Datos](05-bateria-y-datos.md) | Consumo energético, almacenamiento, volúmenes, tiempos de transferencia |
| 5 | [Escenarios y Seguridad](06-escenarios-y-seguridad.md) | Escenarios críticos, seguridad, regulatorio |
| 6 | [Canal SIM — Arquitectura Principal](07-sim-celular.md) | SIM7080G, SD buffer, envío batch periódico directo al backend |
