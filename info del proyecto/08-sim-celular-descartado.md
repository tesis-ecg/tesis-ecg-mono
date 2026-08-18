# Canal SIM (LTE-M) — OPCIÓN DESCARTADA

> [!WARNING]
> **Documento archivado. Esta NO es la arquitectura del proyecto.**
>
> El canal celular fue la decisión original y se descartó. El canal de comunicación
> actual es **WiFi del domicilio del paciente** — ver [Canal WiFi y provisioning](07-wifi-y-provisioning.md)
> y la [justificación de la decisión](01-justificacion.md#opción-b--sim-celular-lte-m--descartada).
>
> Se conserva porque documenta una alternativa evaluada en profundidad (hardware,
> protocolo AT, consumo, costos de plan de datos) y sigue siendo la vía de evolución
> natural si en el futuro se requiere cobertura fuera del domicilio. Todo el contenido
> de abajo describe la arquitectura anterior y **está desactualizado** respecto del
> resto de la documentación.

## Resumen

El Holter opera como dispositivo standalone usando un módulo celular IoT (LTE-M, **SIM7080G como candidato técnico**) con tarjeta SIM para enviar datos ECG directamente al backend cada hora, sin depender de BLE, WiFi del paciente ni smartphone.

La SD card actúa como buffer primario: graba continuamente y, al cumplirse el intervalo (default: 1h), el módulo SIM se despierta, envía el batch acumulado al backend via HTTP POST, y una vez confirmada la recepción, elimina los datos enviados de la SD.

Cada hora, el batch comprimido se envía al backend y, una vez confirmada la recepción (HTTP 200), se libera del buffer local. En operación normal el buffer retiene solo ~1,7 MB (el batch de la hora en curso).

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
| Consumo TX | ~80 mA medios, **pico de 250 mA** a 23 dBm (exige capacitor de bulk y un rail capaz de entregarlo) |
| Consumo idle | ~3-5 mA |
| Consumo PSM (deep sleep) | ~3 µA |
| Costo módulo | ~$8,63-11,11 USD (LCSC, ago. 2026). **Con portasim, antena LTE, bulk cap y regulador de 2 A: ~USD 10,70-13,20/equipo** |
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
| 1NCE | 500 MB / 10 años | $10 USD (una vez) | Solo para pruebas — con el caudal real (40,5 MB/día) se agota en **~12 días** |
| Hologram | Pay-as-you-go | ~$0.60/MB | Roaming global, flexible |
| **Claro Argentina IoT** | Plan IoT M2M 3-5 GB/mes | ~$5-10/mes | **Recomendado para MVP/producción** |
| Movistar Argentina | Plan IoT 3-5 GB/mes | ~$5-10/mes | Cobertura Cat-M1 |

**Recomendación**: con el caudal real el consumo es **~44,6 MB/día (~1,34 GB/mes)** en un estudio de ECG. Se necesita un plan de al menos **2 GB/mes**, que ningún plan M2M argentino ofrece — hay que ir a tarifa de consumidor (~$2.000-3.000 ARS por equipo/mes). 1NCE solo alcanza para pruebas cortas de laboratorio (~12 días).

---

## Consumo de datos celulares

> **Cifras corregidas.** La versión original estimaba 250 Hz × 3 canales × 16 bits con delta encoding al 50%. El firmware real muestrea a **500 Hz** y comprime **sin pérdida** con un codec Rice de predictor de orden 2, con ratio **medido de 12,80×** sobre ruido ambulatorio real.

ECG, 1 derivación, 500 Hz, 24 bits → **468,6 B/s comprimidos** (medición del firmware, `DATAFLOW.md` §9.1)

| Período | Datos comprimidos | Con overhead HTTP/TLS (~10%) |
|---|---|---|
| 1 hora | 1,687 MB | 1,856 MB |
| 1 día | **40,5 MB** | **44,6 MB** |
| 1 mes | 1,22 GB | **1,34 GB** |

**Con envío cada 1h**: ~44,6 MB/día, **~1,34 GB/mes por equipo**.

Y acá está el problema de fondo del canal celular en Argentina: **los planes M2M/IoT locales no cubren ese volumen.** La oferta multicarrier típica (Movistar + Claro) ronda los **20 MB/mes** — unas 60 veces menos. Hay que contratar un plan de datos de consumidor, que es lo mismo que decir que cada chaleco lleva algo parecido a la línea de un celular. Ver el desglose de costos en [09-comparativa-canales-de-transmision.md](09-comparativa-canales-de-transmision.md).

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
2. **PREPARING_BATCH**: Timer expiró. Lee del buffer las tramas pendientes, ya comprimidas por el codec sin pérdida del firmware.
3. **SIM_WAKING**: Envía pulso de `PWRKEY`, espera respuesta `AT`, verifica SIM, conecta a red (~5-15 seg).
4. **SENDING**: HTTP POST del batch al backend. Timeout de 30 seg por request.
5. **CONFIRMING**: Verifica HTTP 200 del backend.
6. **CLEANING_SD**: Elimina los archivos de SD cuyos datos fueron confirmados por el backend.
7. **SIM_SLEEPING**: Envía el módulo a PSM (~3 µA). Vuelve a RECORDING.
8. **SIM_ERROR**: Si falla conexión o envío, los datos permanecen en el buffer local. Se reintenta en el próximo ciclo. Después de 3 fallos consecutivos, incrementa el intervalo (backoff exponencial: 1h → 2h → 4h).

---

## Consumo energético

> ### ⚠️ Corrección de un error de esta sección
>
> La versión original de este documento estimaba el ciclo de envío en **~30 segundos por hora**, y de ahí concluía que "el módulo SIM agrega solo ~0,6 mA" y que el impacto en la autonomía era menor al 5%. **Eso está mal por aproximadamente un factor de 10.**
>
> Subir el batch de una hora (1,86 MB con overhead) en 30 segundos exigiría **~720 kbps sostenidos de subida**. LTE-M no llega ni cerca: la especificación del SIM7080G declara 1.119 kbps como techo teórico, el valor típico citado para Cat-M1 es ~380 kbps, y lo observado en campo va de **25 a 200 kbps** según cobertura.
>
> LTE-M está diseñada para **telemetría** —unos pocos kilobytes de un sensor, con muy bajo consumo y buena penetración en interiores—, no para mover decenas de megabytes por día. Ese desajuste es el problema de fondo de esta opción, y no se resuelve cambiando de módulo.
>
> Las cifras corregidas están abajo. El análisis completo, con las tres opciones comparadas, está en [09-comparativa-canales-de-transmision.md](09-comparativa-canales-de-transmision.md).

| Estado | Consumo | Duración típica | Frecuencia |
|---|---|---|---|
| RECORDING (XIAO nRF52840 + AFE + buffer) | ~6 mA | Continuo | Siempre |
| SIM_WAKING (salir de PSM y engancharse a la red) | ~50 mA | ~10 seg | Cada 1h |
| SENDING (HTTP POST) | ~80 mA medios (pico 250 mA) | **~2,5 a 60 min** según cobertura | Cada 1h |
| SIM_SLEEPING (PSM) | ~3 µA | El resto | Cada 1h |

### Cálculo de autonomía (corregido)

Base: caudal medido de **40,5 MB/día** en un estudio de ECG (+10% de overhead = 44,6 MB/día), consumo base de **6 mA** y batería de **1800 mAh → 1530 mAh útiles**.

```
Cobertura BUENA (380 kbps = 47,5 KB/s)
  Radio en TX = 44,6e6 ÷ 47,5e3 = 939 s/día (15,6 min/día)
  Energía     = 80 mA × 939 s = 20,9 mAh + 3,3 mAh de enganches = 24,2 mAh/día
  Canal SIM   = 1,01 mA  →  total 7,01 mA  →  1530 ÷ 7,01 = 218 h = 9,1 días

Cobertura TÍPICA (100 kbps = 12,5 KB/s)
  Radio en TX = 44,6e6 ÷ 12,5e3 = 3.568 s/día (59,5 min/día)
  Energía     = 79,3 mAh + 3,3 mAh = 82,6 mAh/día
  Canal SIM   = 3,44 mA  →  total 9,44 mA  →  1530 ÷ 9,44 = 162 h = 6,8 días

Cobertura POBRE (25 kbps = 3,1 KB/s)
  Radio en TX = 44,6e6 ÷ 3,1e3 = 14.272 s/día (¡4 horas/día!)
  Energía     = 317,2 mAh + 3,3 mAh = 320,5 mAh/día
  Canal SIM   = 13,35 mA →  total 19,35 mA →  1530 ÷ 19,35 = 79 h = 3,3 días
```

**El módulo SIM agrega entre 1,01 y 13,35 mA**, es decir entre el **14% y el 69% de la autonomía** del equipo — no el 5% que decía la versión anterior.

Y lo más problemático no es el promedio sino la **dispersión**: la autonomía va de 9,1 a 3,3 días según en qué parte de la casa esté el paciente. Un equipo médico cuya batería dura entre 3 y 9 días según la cobertura es un equipo del que no se le puede decir al paciente cuándo cargarlo.

> **Nota sobre el PSM.** El Power Saving Mode funciona muy bien y hace que el consumo *entre* envíos sea irrelevante (3 µA). El problema nunca fue el reposo: es cuánto tiempo hay que tener la radio prendida transmitiendo. Agrandar el batch tampoco ayuda — el costo de enganche (0,139 mAh) es despreciable frente al de transmisión (79,3 mAh/día), así que pasar de 24 envíos diarios a 6 ahorra un 3%.

### Costo recurrente en Argentina

```
Estudio de ECG:        44,6 MB/día × 30 = 1,34 GB por mes y por equipo
Estudio de impedancia:  14,9 MB/día × 30 = 0,45 GB por mes y por equipo
```

Los planes M2M/IoT argentinos están dimensionados para telemetría (~20 MB/mes), unas 60 veces menos de lo que necesita un estudio de ECG, así que habría que contratar un **plan de datos de consumidor**: tomando Movistar prepago como referencia ($1.490/1 GB, $6.100/5 GB), son **~$2.000-3.000 ARS por equipo y por mes**, o ~$100.000-150.000 ARS/mes para un trial de 50 equipos.

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
- El buffer sigue acumulando normalmente (~1,687 MB/hora comprimido, 1 derivación)
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
