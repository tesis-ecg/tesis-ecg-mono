# Capa 1 — Firmware del Holter (XIAO nRF52840 + co-procesador WiFi)

> **Reparto de responsabilidades.** El **XIAO nRF52840** hace todo el trabajo clínico: adquisición, filtrado, detección de QRS, compresión y escritura al buffer local. El **co-procesador ESP32-C3**, conectado por UART, hace solo dos cosas: hablar WiFi/TLS con el backend y servir el portal cautivo de configuración. El resto del tiempo está **apagado** (alimentación cortada por un load switch), no en modo bajo consumo.
>
> Este reparto es deliberado: conserva el firmware ya validado sobre nRF52840 y evita el consumo base de un ESP32 grabando las 24 horas. Ver [la comparativa de canales](09-comparativa-canales-de-transmision.md) para las cuentas.

## Flujo de datos

1. El AFE (ADS1292R) muestrea a **500 Hz, 24 bits**. Exporta 72 bits cada 2 ms: 24 de estado de electrodos, 24 de ECG y 24 de impedancia. **ECG e impedancia son estudios separados**, no simultáneos
2. El firmware filtra, detecta QRS y comprime con un **codec Rice sin pérdida** (predictor de orden 2), y escribe tramas autocontenidas de 256 B al buffer local. Ratio **medido: 12,80×** sobre ruido ambulatorio real → **468,6 B/s = 1,687 MB/hora = 40,5 MB/día**
3. Cada hora: el nRF52840 alimenta el co-procesador, éste se conecta a la red del domicilio y envía el batch al backend vía HTTPS POST. Tras confirmación HTTP 200, se liberan del buffer los datos enviados y se corta la alimentación del co-procesador
4. El dispositivo funciona standalone — no requiere smartphone ni app. El celular del paciente interviene una sola vez, en la configuración inicial de la red (ver [Canal WiFi y provisioning](07-wifi-y-provisioning.md))

## Almacenamiento local

> **Estado actual: flash SPI de 16 MB, no microSD.** El hardware lleva una **S25FL128L de 16 MB** organizada como log circular de tramas de 256 B, que da **9,94 horas** de grabación. La **microSD de 4-8 GB es un requerimiento abierto** hacia el equipo de Biomédica, no algo que ya exista. Es el riesgo más grande del sistema: con 10 horas de buffer, un paciente que sale a trabajar un día completo pierde señal.

- **Formato**: tramas autocontenidas de 256 B, cada una con magic, versión, número de secuencia monótono y CRC-32. Una trama dañada se descarta y se pierde ~medio segundo, no el estudio
- **Agrupación de envío**: las tramas se agrupan por bloques de una hora para el batch (~1,687 MB)
- **Ciclo normal**: cada hora se sube el bloque y se liberan del log solo las tramas confirmadas
- **Fuera del alcance del WiFi**: el buffer acumula ~40,5 MB/día. Con la flash actual eso son **~10 h**; con la microSD pendiente, ~4 meses
- **Rotación FIFO**: al llegar al límite se pisan las tramas más antiguas no confirmadas, y el firmware lo reporta con `STATUS_FLAG_BACKLOG_OVERFLOW`

## Compresión

- **Codec Rice sin pérdida** con predictor de orden 2 — reconstruye bit a bit la señal cruda y los flags. No es un compresor con pérdida tipo AZTEC/TP/CORTES: ésos distorsionan justamente las ondas de baja amplitud como la P
- Ratio **medido**, no estimado: **12,80×** sobre ruido ambulatorio real (MIT-BIH Noise Stress Test) y 14,55× sobre señal limpia. Se verifica muestra a muestra en cada corrida de tests del firmware
- **40,5 MB/día** en un estudio de ECG de 24 h (~1,687 MB/hora)
- Se aplica antes de escribir al buffer; el batch que se envía por WiFi son las tramas ya comprimidas
- El decodificador de referencia en Python vive en `tools/holter_frame_decoder.py` del repo de firmware

## Canal WiFi — Canal principal de comunicación

El Holter opera como dispositivo standalone usando el **WiFi del domicilio del paciente**. La **SD card actúa como buffer primario**: graba datos continuamente y cada intervalo configurable (default: 1 hora), el MCU enciende la radio, envía el batch acumulado al backend via HTTPS POST, y tras confirmación del servidor, elimina los datos enviados de la SD.

La red del domicilio se configura desde el propio dispositivo mediante **SoftAP + portal cautivo**, sin app móvil. El dispositivo mantiene **dos slots de credenciales** en NVS: la red de la clínica (permanente) y la red del paciente (rotativa por estudio).

**Ver documentación completa en [Canal WiFi y provisioning](07-wifi-y-provisioning.md).**

### Radio

```
nRF52840 --UART--> co-procesador ESP32-C3 (WiFi 2.4 GHz) → router del domicilio → internet → FastAPI
```

El co-procesador está **sin alimentación** durante el estado `RECORDING` y solo se enciende para el ciclo de envío (~4 seg de enganche + ~4 seg de transmisión por hora; ~90 segundos por día en total).

### Máquina de estados

```
RECORDING → [timer 1h] → PREPARING_BATCH → WIFI_CONNECTING → SENDING → CONFIRMING → CLEANING_SD → WIFI_OFF → RECORDING
                                                   ↓ (sin red)              ↓ (fallo)
                                              WIFI_ERROR ←─────────────────┘
                                                   ↓
                                              WIFI_OFF → RECORDING (reintento en próximo ciclo)
```

1. **RECORDING**: ECG → RAM → buffer local, trama a trama (estado normal, continuo, co-procesador sin alimentación)
2. **PREPARING_BATCH**: el nRF52840 lee del buffer las tramas pendientes (más antiguas primero), ya comprimidas
3. **WIFI_CONNECTING**: alimenta el co-procesador, que asocia y pide DHCP. Prueba slot 1 (paciente), luego slot 0 (clínica). ~4 seg incluyendo el handshake TLS
4. **SENDING**: HTTPS POST al backend con datos comprimidos en base64
5. **CONFIRMING**: Verifica HTTP 200 del backend
6. **CLEANING_SD**: libera del buffer solo las tramas confirmadas por el backend
7. **WIFI_OFF**: corta la alimentación del co-procesador (load switch, no modem sleep)
8. **WIFI_ERROR**: Fuera de alcance, router caído o fallo de envío → los datos permanecen en el buffer, reintento en próximo ciclo (backoff exponencial después de 3 fallos: 1h → 2h → 4h)

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
      "sample_rate": 500,
      "num_samples": 1800000,
      "compression": "rice-p2",
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
