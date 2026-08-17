# Arquitectura de Comunicación: Holter Wearable ↔ Cloud

Arquitectura de 2 capas para un wearable ECG modular (chaleco/top) con AFE + MCU familia ESP32, orientado a monitoreo cardíaco preventivo continuo (semanas/meses) para pacientes de 40-70 años en Argentina.

## Decisión principal

**WiFi del domicilio del paciente como único canal de comunicación, arquitectura standalone.** El dispositivo envía datos ECG directamente al backend cada hora vía HTTPS, sin BLE, sin app móvil y sin módulo celular. La red del domicilio se configura desde el propio dispositivo mediante SoftAP + portal cautivo, sin instalar nada. El MCU concreto (familia ESP32 — XIAO ESP32-C3 / C6 / S3 como candidatos) se confirma en Fase 1. Ver [justificación completa](01-justificacion.md).

> **Cambio respecto del diseño original.** El proyecto arrancó con un módulo celular SIM (LTE-M) como canal único. Esa opción se descartó al quitarse el módulo SIM del diseño de hardware — el análisis completo se conserva en [08-sim-celular-descartado.md](08-sim-celular-descartado.md) y sigue siendo la vía de evolución si el producto llegara a requerir cobertura fuera del domicilio.

## Diagrama general

```
┌──────────────────────────────────────┐
│         HOLTER                        │
│         (ESP32 + AFE)                 │
│                                        │
│      ECG ADC (3 canales)              │
│              ↓                         │
│          SD Card (4-8 GB)              │
│       (buffer primario,               │
│        always-on)                     │
│              ↓ cada 1h                 │
│         WiFi 2.4 GHz                  │
│      (radio apagada entre envíos)     │
└──────────────┬─────────────────────────┘
               │ HTTPS (TLS 1.2+)
               ▼
       router del domicilio
               │
               ▼
┌──────────────────────────┐
│    CLOUD                  │
│                            │
│  API REST (FastAPI)        │
│  PostgreSQL + S3           │
│  Dashboard Médico (Web)    │
└────────────────────────────┘
```

Configuración inicial (una sola vez, en la entrega):

```
Holter (modo AP) ←── celular del paciente ──→ portal cautivo → SSID + password → NVS
```

## Flujo de datos por escenario

```
Estado normal:       Holter → SD (buffer) → WiFi --HTTPS--> Cloud (batch cada 1h)
Fuera del domicilio: Holter → SD acumula (meses de margen con SD de 4-8 GB). Al volver: envía pendientes
Router caído:        Igual que fuera del domicilio. Reintento en el próximo ciclo
Fallo de envío:      Datos permanecen en SD. Backoff exponencial (1h → 2h → 4h)
```

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| MCU | Familia ESP32 con WiFi 2.4 GHz integrado (modelo a confirmar en Fase 1) |
| Almacenamiento | microSD 4-8 GB via SPI (~2-4 meses de buffer sin conexión) |
| Batería | Li-Po 800-1000 mAh (autonomía a validar con medición real) |
| Comunicación | WiFi 2.4 GHz → HTTPS |
| Provisioning | SoftAP + portal cautivo (ESP-IDF `wifi_provisioning`, `scheme_softap`) |
| Backend | Python FastAPI |
| DB | PostgreSQL |
| Storage | S3 (AWS) o GCS |
| Dashboard | React / Next.js |
| Hosting (MVP) | Railway o Render |

## Documentos

| # | Documento | Contenido |
|---|---|---|
| 1 | [Justificación — Arquitectura WiFi](01-justificacion.md) | Comparación de opciones, factores decisivos, desventajas aceptadas |
| 2 | [Firmware del Holter](02-firmware-holter.md) | Flujo de datos, SD, máquina de estados |
| 3 | [App móvil](03-app-movil.md) | Por qué no se desarrolla app |
| 4 | [Cloud y Dashboard](04-cloud.md) | FastAPI, modelo de datos, dashboard médico |
| 5 | [Batería y Datos](05-bateria-y-datos.md) | Consumo energético, almacenamiento, volúmenes, tiempos de transferencia |
| 6 | [Escenarios y Seguridad](06-escenarios-y-seguridad.md) | Escenarios críticos, seguridad, regulatorio |
| 7 | [Canal WiFi y Provisioning](07-wifi-y-provisioning.md) | SoftAP, portal cautivo, slots de credenciales, re-provisioning, ciclo de envío |
| 8 | [Canal SIM — DESCARTADO](08-sim-celular-descartado.md) | Archivo histórico: análisis del canal celular LTE-M |
