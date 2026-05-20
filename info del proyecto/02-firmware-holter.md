# Capa 1 — Firmware del Holter (XIAO Nordic)

## Flujo de datos

1. AFE muestrea ECG en 3 canales (sample rate y resolución a definir con el equipo de Biomédica en Fase 1; estimación de referencia: 250 Hz × 16 bits → ~1.500 bytes/seg → ~129 MB/día raw)
2. Firmware aplica delta encoding y escribe datos comprimidos a microSD continuamente (buffer en RAM, flush cada 4-8 seg) → ~65 MB/día comprimido (~2.7 MB/hora)
3. Cada hora: el módulo SIM se despierta, envía el batch comprimido de la SD al backend via HTTP POST (LTE-M), espera confirmación HTTP 200 del servidor y borra los datos enviados de la SD
4. El dispositivo funciona 100% standalone — no requiere smartphone, BLE ni WiFi

## Almacenamiento en SD

- **Formato**: Archivos binarios por hora (ej: `2026-03-19_14.bin`)
- **Header**: metadata del dispositivo, configuración ADC, versión de firmware
- **Ciclo normal**: cada hora se sube el archivo a la nube y se borra de la SD — la SD retiene solo ~2.7 MB en operación normal
- **Sin señal celular**: la SD acumula hasta ~2 días antes de llenarse (128 MB / 2.7 MB/h ≈ 47 horas)
- **Rotación FIFO**: si la SD llega al 90% de capacidad, se borran los archivos más antiguos no enviados

## Compresión

- Delta encoding + compresión simple → reduce ~50% del volumen
- 129 MB/día raw → ~65 MB/día comprimido (~2.7 MB/hora)
- Se aplica antes de escribir a SD; el batch que se envía por SIM son los datos ya comprimidos de la SD

## Módulo celular SIM — Canal principal de comunicación

El Holter opera como dispositivo standalone usando un módulo celular IoT (LTE-M/NB-IoT, **SIM7080G como candidato técnico**) con tarjeta SIM. La **SD card actúa como buffer primario**: graba datos continuamente y cada intervalo configurable (default: 1 hora), el módulo SIM se despierta, envía el batch acumulado al backend via HTTP POST, y tras confirmación del servidor, elimina los datos enviados de la SD.

**Ver documentación completa en [Canal SIM](07-sim-celular.md).**

### Conexión hardware

```
XIAO Nordic (UART) ←→ Módulo SIM (LTE-M) → red celular → FastAPI
```

### Máquina de estados

```
RECORDING → [timer 1h] → PREPARING_BATCH → SIM_WAKING → SENDING → CONFIRMING → CLEANING_SD → SIM_SLEEPING → RECORDING
                                                                        ↓ (fallo)
                                                                   SIM_ERROR → SIM_SLEEPING → RECORDING (reintento en próximo ciclo)
```

1. **RECORDING**: ECG → RAM → SD cada 4-8 seg (estado normal, continuo)
2. **PREPARING_BATCH**: Lee archivos pendientes de SD, comprime con delta encoding
3. **SIM_WAKING**: Enciende el módulo SIM, conecta a red LTE-M (~10 seg)
4. **SENDING**: HTTP POST al backend con datos comprimidos en base64
5. **CONFIRMING**: Verifica HTTP 200 del backend
6. **CLEANING_SD**: Elimina de SD solo los datos confirmados por el backend
7. **SIM_SLEEPING**: Módulo SIM entra en PSM (~3 µA)
8. **SIM_ERROR**: Fallo de red/envío → datos permanecen en SD, reintento en próximo ciclo (backoff exponencial después de 3 fallos: 1h → 2h → 4h)

### Formato del payload HTTP

```json
{
  "device_id": "holter-001",
  "firmware_version": "1.0.0",
  "battery_pct": 72,
  "sd_free_mb": 120,
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

### Endpoint del backend

```
POST /devices/{device_id}/ecg-batch
Header: X-API-Key: <device-api-key>
```

Ver detalles del endpoint en [Cloud y Dashboard](04-cloud.md#recepción-directa-desde-holter).
