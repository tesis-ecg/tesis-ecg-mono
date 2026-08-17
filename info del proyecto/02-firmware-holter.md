# Capa 1 — Firmware del Holter (MCU familia ESP32)

> **MCU a confirmar en Fase 1.** La arquitectura requiere WiFi integrado, lo que ubica al MCU en la familia **ESP32** (candidatos: XIAO ESP32-C3 / C6 / S3). El modelo concreto se define con el equipo de Biomédica según RAM, consumo y disponibilidad. Todas las cifras de consumo son estimaciones de familia, a validar con medición real.

## Flujo de datos

1. AFE muestrea ECG en 3 canales (sample rate y resolución a definir con el equipo de Biomédica en Fase 1; estimación de referencia: 250 Hz × 16 bits → ~1.500 bytes/seg → ~129 MB/día raw)
2. Firmware aplica delta encoding y escribe datos comprimidos a microSD continuamente (buffer en RAM, flush cada 4-8 seg) → ~65 MB/día comprimido (~2.7 MB/hora)
3. Cada hora: el MCU enciende la radio WiFi, se conecta a la red configurada del domicilio, envía el batch comprimido de la SD al backend via HTTPS POST, espera confirmación HTTP 200 y borra los datos enviados de la SD
4. El dispositivo funciona standalone — no requiere smartphone, app ni BLE. El celular del paciente interviene una sola vez, en la configuración inicial de la red (ver [Canal WiFi y provisioning](07-wifi-y-provisioning.md))

## Almacenamiento en SD

- **Formato**: Archivos binarios por hora (ej: `2026-03-19_14.bin`)
- **Header**: metadata del dispositivo, configuración ADC, versión de firmware
- **Ciclo normal**: cada hora se sube el archivo a la nube y se borra de la SD — la SD retiene solo ~2.7 MB en operación normal
- **Fuera del alcance del WiFi**: la SD acumula ~65 MB/día. Con la SD dimensionada según [Batería y datos](05-bateria-y-datos.md), la ventana cubierta es de meses, no de horas
- **Rotación FIFO**: si la SD llega al 90% de capacidad, se borran los archivos más antiguos no enviados

## Compresión

- Delta encoding + compresión simple → reduce ~50% del volumen
- 129 MB/día raw → ~65 MB/día comprimido (~2.7 MB/hora)
- Se aplica antes de escribir a SD; el batch que se envía por WiFi son los datos ya comprimidos de la SD

## Canal WiFi — Canal principal de comunicación

El Holter opera como dispositivo standalone usando el **WiFi del domicilio del paciente**. La **SD card actúa como buffer primario**: graba datos continuamente y cada intervalo configurable (default: 1 hora), el MCU enciende la radio, envía el batch acumulado al backend via HTTPS POST, y tras confirmación del servidor, elimina los datos enviados de la SD.

La red del domicilio se configura desde el propio dispositivo mediante **SoftAP + portal cautivo**, sin app móvil. El dispositivo mantiene **dos slots de credenciales** en NVS: la red de la clínica (permanente) y la red del paciente (rotativa por estudio).

**Ver documentación completa en [Canal WiFi y provisioning](07-wifi-y-provisioning.md).**

### Radio

```
MCU ESP32 (WiFi 2.4 GHz) → router del domicilio → internet → FastAPI
```

La radio está apagada durante el estado `RECORDING` y solo se enciende para el ciclo de envío (~15-20 seg por hora).

### Máquina de estados

```
RECORDING → [timer 1h] → PREPARING_BATCH → WIFI_CONNECTING → SENDING → CONFIRMING → CLEANING_SD → WIFI_OFF → RECORDING
                                                   ↓ (sin red)              ↓ (fallo)
                                              WIFI_ERROR ←─────────────────┘
                                                   ↓
                                              WIFI_OFF → RECORDING (reintento en próximo ciclo)
```

1. **RECORDING**: ECG → RAM → SD cada 4-8 seg (estado normal, continuo, radio apagada)
2. **PREPARING_BATCH**: Lee archivos pendientes de SD (más antiguos primero), ya comprimidos con delta encoding
3. **WIFI_CONNECTING**: Enciende la radio, asocia y pide DHCP. Prueba slot 1 (paciente), luego slot 0 (clínica). ~2-5 seg
4. **SENDING**: HTTPS POST al backend con datos comprimidos en base64
5. **CONFIRMING**: Verifica HTTP 200 del backend
6. **CLEANING_SD**: Elimina de SD solo los datos confirmados por el backend
7. **WIFI_OFF**: Apaga la radio (modem sleep)
8. **WIFI_ERROR**: Fuera de alcance, router caído o fallo de envío → datos permanecen en SD, reintento en próximo ciclo (backoff exponencial después de 3 fallos: 1h → 2h → 4h)

Estado adicional fuera de este ciclo: **PROVISIONING** — el dispositivo levanta el SoftAP y sirve el portal cautivo. Se entra al arrancar sin credenciales, por botón físico o por orden del backend. Ver [Canal WiFi y provisioning](07-wifi-y-provisioning.md#provisioning-inicial--softap--portal-cautivo).

### Formato del payload HTTP

```json
{
  "device_id": "holter-001",
  "firmware_version": "1.0.0",
  "battery_pct": 72,
  "sd_free_mb": 120,
  "wifi_rssi": -58,
  "batch": [
    {
      "timestamp": 1713200000,
      "duration_sec": 3600,
      "sample_rate": 250,
      "num_samples": 900000,
      "compression": "delta",
      "data_b64": "...(datos ECG comprimidos en base64)..."
    }
  ]
}
```

`wifi_rssi` reemplaza al indicador de señal celular del diseño anterior y alimenta el Tablero de Salud del Hardware en el dashboard.

### Endpoint del backend

```
POST /devices/{device_id}/ecg-batch
Header: X-API-Key: <device-api-key>
```

Ver detalles del endpoint en [Cloud y Dashboard](04-cloud.md#recepción-directa-desde-holter).
