# Canal SIM — Arquitectura principal de comunicación

## Resumen

El Holter opera como dispositivo standalone usando un módulo celular IoT (LTE-M, **SIM7080G como candidato técnico**) con tarjeta SIM para enviar datos ECG directamente al backend cada hora, sin depender de BLE, WiFi del paciente ni smartphone. **Este es el canal primario del sistema.**

La SD card actúa como buffer primario: graba continuamente y, al cumplirse el intervalo (default: 1h), el módulo SIM se despierta, envía el batch acumulado al backend via HTTP POST, y una vez confirmada la recepción, elimina los datos enviados de la SD.

Cada hora, el batch comprimido de la SD se envía al backend y, una vez confirmada la recepción (HTTP 200), se borra de la SD. En operación normal la SD retiene solo ~2.7 MB (el batch de la hora en curso).

---

## Casos de uso

- **Uso estándar**: todos los pacientes — el dispositivo envía datos al backend de forma autónoma
- Pacientes sin smartphone o con dificultades tecnológicas
- Zonas sin WiFi disponible (rurales, al aire libre, en tránsito)
- Monitoreo completamente autónomo sin intervención del paciente

---

## Hardware

### Módulo candidato: SIM7080G (SIMCOM)

> Este es el módulo de referencia técnica más probable para el proyecto. La selección final se confirma en Fase 1 según disponibilidad y compatibilidad con el XIAO Nordic.

| Parámetro | Valor |
|---|---|
| Tecnología | LTE-M (Cat-M1) + NB-IoT + GNSS |
| Interfaz con MCU | UART (TX/RX + control pins) |
| Voltaje operación | 3.0 - 4.3V (compatible con LiPo directo) |
| Consumo TX | ~50-80 mA |
| Consumo idle | ~3-5 mA |
| Consumo PSM (deep sleep) | ~3 µA |
| Costo módulo | ~$8-12 USD |
| Disponibilidad Argentina | Mercado Libre, Electrocomponentes, Todobytes |
| Tamaño | 17.6 x 15.7 x 2.3 mm |

### Conexión al XIAO Nordic

```
XIAO Nordic                 SIM7080G
─────────                   ────────
GPIO_TX  ──────────────────  RXD
GPIO_RX  ──────────────────  TXD
GPIO_PWR ──────────────────  PWRKEY (encendido/apagado)
GPIO_DTR ──────────────────  DTR (control de sleep)
GND      ──────────────────  GND
VBAT     ──────────────────  VBAT (3.7V LiPo directo)
```

### Componentes adicionales en PCB

- **SIM slot**: Nano-SIM o eSIM (según disponibilidad)
- **Antena LTE**: Antena cerámica o PCB (~15x5 mm) con conector U.FL o directa al pad
- **Capacitor de bypass**: 100 µF cerca de VBAT del módulo SIM (picos de corriente TX)

### SIM recomendada

| Proveedor | Plan | Costo | Notas |
|---|---|---|---|
| 1NCE | 500 MB / 10 años | $10 USD (una vez) | Solo para pruebas (~6 días de datos a 3 canales) |
| Hologram | Pay-as-you-go | ~$0.60/MB | Roaming global, flexible |
| **Claro Argentina IoT** | Plan IoT M2M 3-5 GB/mes | ~$5-10/mes | **Recomendado para MVP/producción** |
| Movistar Argentina | Plan IoT 3-5 GB/mes | ~$5-10/mes | Cobertura Cat-M1 |

**Recomendación**: Con 3 canales el consumo es ~78 MB/día (~2.4 GB/mes). Se necesita un plan de al menos **3 GB/mes**. 1NCE solo alcanza para pruebas cortas de laboratorio.

---

## Consumo de datos celulares

3 canales (parámetros de muestreo a confirmar en Fase 1; estimación de referencia 250 Hz × 16 bits) → ~1.500 bytes/seg raw → ~2.7 MB/hora comprimido (~50% delta encoding)

| Período | Datos comprimidos | Con overhead HTTP/TLS (~20%) |
|---|---|---|
| 1 hora | ~2.7 MB | ~3.2 MB |
| 1 día | ~65 MB | ~78 MB |
| 1 mes | ~1.95 GB | ~2.4 GB |

**Con envío cada 1h**: ~78 MB/día, ~2.4 GB/mes. Se requiere un plan IoT de mínimo **3 GB/mes**.

---

## Protocolo de comunicación

### Secuencia AT → HTTP POST

El SIM7080G (candidato técnico) soporta HTTP/HTTPS nativo via comandos AT. La secuencia de referencia es:

```
// 1. Despertar módulo
AT+CSCLK=0                          // Salir de sleep
AT+CPIN?                             // Verificar SIM lista

// 2. Conectar a red
AT+CNACT=0,1                         // Activar PDP context
AT+CNACT?                            // Verificar IP asignada

// 3. Configurar HTTP
AT+SHCONF="URL","https://api.holter.com/devices/holter-001/ecg-batch"
AT+SHCONF="BODYLEN",65536
AT+SHCONF="HEADERLEN",350
AT+SHSSL=1,""                        // Habilitar TLS

// 4. Conectar y enviar
AT+SHCONN                            // Abrir conexión
AT+SHCHEAD                           // Limpiar headers
AT+SHAHEAD="Content-Type","application/json"
AT+SHAHEAD="X-API-Key","device-secret-key"
AT+SHBOD=<body_len>,10000            // Preparar body
> {json payload}                      // Enviar datos
AT+SHREQ="/devices/holter-001/ecg-batch",3  // POST request

// 5. Leer respuesta
AT+SHREAD=0,200                      // Leer response body
// Si status 200 → datos recibidos OK → borrar de SD

// 6. Cerrar y dormir
AT+SHDISC                            // Cerrar conexión
AT+CNACT=0,0                         // Desactivar PDP
AT+CSCLK=2                           // Entrar en PSM
```

### Payload HTTP

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
      "data_b64": "...(datos comprimidos en base64)..."
    }
  ]
}
```

**Nota**: Si el batch es mayor al límite del buffer HTTP del módulo SIM (~64 KB en el SIM7080G), se fragmenta en múltiples requests con un campo `chunk_index` / `total_chunks`.

---

## Ciclo de vida — Máquina de estados

```
┌─────────────────────────────────────────────────┐
│                                                   │
│   ┌──────────┐    timer    ┌──────────────┐      │
│   │ RECORDING │──────────>│ PREPARING_BATCH│      │
│   │ (SD write)│            │ (read SD,     │      │
│   └──────────┘            │  compress)    │      │
│        ↑                   └──────┬───────┘      │
│        │                          │               │
│        │                          ▼               │
│        │                  ┌──────────────┐       │
│        │                  │ SIM_WAKING    │       │
│        │                  │ (power on,    │       │
│        │                  │  connect net) │       │
│        │                  └──────┬───────┘       │
│        │                         │                │
│        │              ┌──────────┴────────┐      │
│        │              │                    │      │
│        │              ▼                    ▼      │
│        │     ┌──────────────┐    ┌────────────┐  │
│        │     │ SENDING      │    │ SIM_ERROR   │  │
│        │     │ (HTTP POST)  │    │ (retry next │  │
│        │     └──────┬───────┘    │  cycle)     │  │
│        │            │            └──────┬─────┘  │
│        │            ▼                    │        │
│        │     ┌──────────────┐           │        │
│        │     │ CONFIRMING   │           │        │
│        │     │ (check 200)  │           │        │
│        │     └──────┬───────┘           │        │
│        │            │                    │        │
│        │            ▼                    │        │
│        │     ┌──────────────┐           │        │
│        │     │ CLEANING_SD  │           │        │
│        │     │ (delete sent)│           │        │
│        │     └──────┬───────┘           │        │
│        │            │                    │        │
│        │            ▼                    │        │
│        │     ┌──────────────┐           │        │
│        │     │ SIM_SLEEPING │◄──────────┘        │
│        │     │ (PSM mode)   │                     │
│        │     └──────┬───────┘                     │
│        │            │                             │
│        └────────────┘                             │
│                                                   │
└─────────────────────────────────────────────────┘
```

### Detalle de cada estado

1. **RECORDING**: Estado normal. ECG → RAM buffer → SD cada 4-8 seg. Timer de intervalo corriendo.
2. **PREPARING_BATCH**: Timer expiró. Lee archivos pendientes de SD, aplica delta encoding + compresión.
3. **SIM_WAKING**: Envía pulso de `PWRKEY`, espera respuesta `AT`, verifica SIM, conecta a red (~5-15 seg).
4. **SENDING**: HTTP POST del batch al backend. Timeout de 30 seg por request.
5. **CONFIRMING**: Verifica HTTP 200 del backend.
6. **CLEANING_SD**: Elimina los archivos de SD cuyos datos fueron confirmados por el backend.
7. **SIM_SLEEPING**: Envía el módulo a PSM (~3 µA). Vuelve a RECORDING.
8. **SIM_ERROR**: Si falla conexión o envío, los datos permanecen en SD. Se reintenta en el próximo ciclo. Después de 3 fallos consecutivos, incrementa el intervalo (backoff exponencial: 1h → 2h → 4h).

---

## Consumo energético

| Estado | Consumo | Duración típica | Frecuencia |
|---|---|---|---|
| RECORDING (XIAO Nordic + AFE + SD) | ~6-12 mA | Continuo | Siempre |
| SIM_WAKING (conexión a red) | ~60 mA | ~10 seg | Cada 1h |
| SENDING (HTTP POST) | ~60-80 mA | ~15-20 seg | Cada 1h |
| SIM_SLEEPING (PSM) | ~3 µA | ~59 min | Cada 1h |

### Cálculo de autonomía

```
Consumo base (XIAO Nordic + AFE + SD, sin SIM): ~10 mA promedio
Consumo SIM promedio/día:                        24 envíos × 30 seg × 70 mA = 50,400 mAs / 86,400 s ≈ 0.6 mA
Total promedio con SIM:                          ~10.6 mA

Batería 500 mAh:  ~47 h (~2 días)
Batería 800 mAh:  ~75 h (~3 días)
```

El módulo SIM agrega solo ~0.6 mA al consumo promedio gracias al PSM. Impacto menor al 5% en autonomía.

---

## Gestión de la SD en modo SIM

### Estructura de archivos

```
/ecg/
  2026-04-16_10.bin    ← archivo de la hora 10:00-10:59
  2026-04-16_11.bin    ← archivo de la hora 11:00-11:59 (en curso)
/sent/
  (vacío — los archivos enviados se eliminan)
/meta/
  last_sent.txt        ← timestamp del último envío exitoso
  pending_count.txt    ← cantidad de archivos pendientes
  send_errors.txt      ← log de errores de envío (últimos 10)
```

### Flujo de limpieza

1. Al confirmar HTTP 200, se marca el archivo como enviado en `last_sent.txt`
2. Se elimina el archivo `.bin` correspondiente de `/ecg/`
3. Si la eliminación falla (SD busy), se reintenta en el próximo ciclo
4. Nunca se elimina el archivo en curso de escritura

### Manejo de acumulación

Si la SIM no puede enviar por horas/días (sin señal, plan sin datos):
- La SD sigue acumulando normalmente (~2.7 MB/hora comprimido, 3 canales)
- Con 128 MB, hay margen para ~2 días de datos sin enviar (~47 horas)
- Al recuperar señal, se envían los batches acumulados secuencialmente (más antiguos primero)
- Se limita a 5 batches por ciclo de envío para no mantener la SIM encendida demasiado tiempo

---

## Seguridad

### En tránsito (SIM → Backend)
- **TLS 1.2** obligatorio (módulos LTE-M como el SIM7080G soportan TLS nativo con AT+SHSSL)
- Certificado del servidor validado contra CA root cargada en el módulo

### Autenticación del dispositivo
- **API key única por dispositivo** enviada en header `X-API-Key`
- La API key se graba en flash (almacenamiento no volátil) del XIAO Nordic durante manufacturing/provisioning
- Rotación de API key posible via comando del backend (en un futuro, si se necesita)

### Datos en reposo (SD)
- Sin encriptar en SD (misma decisión que en el diseño original — ver [escenarios y seguridad](06-escenarios-y-seguridad.md))
- El dispositivo es físicamente del paciente

---

## Provisioning (configuración inicial)

El APN y credenciales de la SIM se configuran en firmware durante la programación inicial del XIAO Nordic:

1. **Pre-configurado en firmware**: APN del operador y API key se graban en flash del XIAO Nordic durante la programación inicial en fábrica/laboratorio

### Parámetros configurables

| Parámetro | Default | Descripción |
|---|---|---|
| `send_interval_min` | 60 | Intervalo de envío en minutos |
| `apn` | "datos.personal.com" | APN del operador |
| `backend_url` | "https://api.holter.com" | URL del backend |
| `api_key` | (generado por dispositivo) | Clave de autenticación |
| `max_retries` | 3 | Reintentos antes de backoff |
| `max_batches_per_cycle` | 5 | Límite de batches por envío |

---

## Limitaciones y trade-offs

| Aspecto | Detalle |
|---|---|
| **Sin monitoreo real-time** | Los datos llegan al backend con delay de hasta 1h (o más si falla el envío) |
| **Sin alertas inmediatas** | Una anomalía cardíaca no se detecta hasta el próximo batch |
| **Costo operativo** | Requiere plan de datos IoT (~$1-10/mes según proveedor) |
| **Complejidad de PCB** | Módulo SIM + SIM slot + antena LTE = más espacio y diseño RF |
| **Cobertura** | Depende de señal LTE-M del operador en la zona del paciente |
| **Sin feedback al paciente** | El paciente no ve su ECG ni recibe alertas en tiempo real; el médico accede vía dashboard web |

### Limitación principal aceptada

El delay máximo de 1h para que los datos lleguen al backend es aceptable para monitoreo preventivo continuo (detección de arritmias sobre historial). No es adecuado para monitoreo de emergencias o ICU donde se requiere latencia de segundos.
