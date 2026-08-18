# Arquitectura de Comunicación: Holter Wearable ↔ Cloud

Arquitectura de 2 capas para un wearable ECG modular (chaleco/top) con AFE ADS1292R + MCU **Seeed XIAO nRF52840**, orientado a monitoreo cardíaco preventivo continuo (semanas/meses) para pacientes de 40-70 años en Argentina.

## Decisión principal

**WiFi del domicilio del paciente como único canal de datos, arquitectura standalone.** El dispositivo envía los datos directamente al backend cada hora vía HTTPS, sin app móvil y sin módulo celular. La red del domicilio se configura desde el propio dispositivo mediante SoftAP + portal cautivo, sin instalar nada.

El MCU es el **XIAO nRF52840**, que es sobre el que está escrito y validado el firmware del equipo de Biomédica. Como ese chip **no tiene WiFi**, la radio la aporta un **co-procesador ESP32-C3** conectado por UART, que se enciende solo durante el ciclo de envío (~90 segundos por día) y está cortado el resto del tiempo. Ver [justificación completa](01-justificacion.md) y las cuentas de consumo y costo en [la comparativa de canales](09-comparativa-canales-de-transmision.md).

> **Cambio respecto del diseño original.** El proyecto arrancó con un módulo celular SIM (LTE-M) como canal único. Esa opción se descartó al quitarse el módulo SIM del diseño de hardware — el análisis completo se conserva en [08-sim-celular-descartado.md](08-sim-celular-descartado.md) y sigue siendo la vía de evolución si el producto llegara a requerir cobertura fuera del domicilio.

## Diagrama general

```
┌──────────────────────────────────────────┐
│         HOLTER                            │
│   XIAO nRF52840 + ADS1292R (AFE)          │
│                                            │
│   ECG / impedancia, 500 Hz, 24 bits       │
│              ↓  (codec sin pérdida 12,8×) │
│      Flash SPI 16 MB — buffer ~10 h        │
│      (microSD 4-8 GB: pendiente)          │
│              ↓ cada 1h, por UART           │
│   Co-procesador ESP32-C3 — WiFi 2.4 GHz   │
│      (alimentación cortada entre envíos)  │
└──────────────┬─────────────────────────────┘
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
Fuera del domicilio: Holter → buffer acumula (~10 h hoy; meses con la microSD pendiente). Al volver: envía pendientes
Router caído:        Igual que fuera del domicilio. Reintento en el próximo ciclo
Fallo de envío:      Datos permanecen en SD. Backoff exponencial (1h → 2h → 4h)
```

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| MCU | Seeed XIAO **nRF52840** (Cortex-M4F + BLE, sin WiFi) |
| AFE | TI **ADS1292R**, 500 Hz, 24 bits, 2 canales |
| Radio WiFi | Co-procesador **ESP32-C3-MINI-1** por UART, con load switch (~USD 2,60/equipo) |
| Almacenamiento | Flash SPI S25FL128L de **16 MB** (~10 h de buffer). **microSD 4-8 GB: requerimiento abierto** |
| Batería | Li-Po 3,7 V **1800 mAh** — autonomía estimada **~10 días** (a validar con medición real) |
| Comunicación | WiFi 2.4 GHz → HTTPS |
| Provisioning | SoftAP + portal cautivo en el co-procesador (ESP-IDF `wifi_provisioning`, `scheme_softap`) |
| Backend | Python FastAPI |
| DB | PostgreSQL |
| Storage | S3 (AWS) o GCS |
| Dashboard | React / Next.js |
| Hosting (MVP) | Railway o Render |

## Documentos

| # | Documento | Contenido |
|---|---|---|
| 1 | [Justificación — Arquitectura WiFi](01-justificacion.md) | Comparación de opciones, factores decisivos, desventajas aceptadas |
| 2 | [Firmware del Holter](02-firmware-holter.md) | Flujo de datos, buffer local, máquina de estados |
| 3 | [App móvil](03-app-movil.md) | Por qué la app no es parte del canal de datos |
| 4 | [Cloud y Dashboard](04-cloud.md) | FastAPI, modelo de datos, dashboard médico |
| 5 | [Batería y Datos](05-bateria-y-datos.md) | Consumo energético, almacenamiento, volúmenes, tiempos de transferencia |
| 6 | [Escenarios y Seguridad](06-escenarios-y-seguridad.md) | Escenarios críticos, seguridad, regulatorio |
| 7 | [Canal WiFi y Provisioning](07-wifi-y-provisioning.md) | SoftAP, portal cautivo, slots de credenciales, re-provisioning, ciclo de envío |
| 8 | [Canal SIM — DESCARTADO](08-sim-celular-descartado.md) | Archivo histórico: análisis del canal celular LTE-M |
| 9 | [Comparativa de canales de transmisión](09-comparativa-canales-de-transmision.md) | **BLE vs WiFi vs SIM: cuentas de consumo, costos de hardware y plan de datos, y conclusión** |
