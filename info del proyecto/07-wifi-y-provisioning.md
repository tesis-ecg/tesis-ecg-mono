# Canal WiFi — Arquitectura principal de comunicación

## Resumen

El Holter usa el **WiFi del domicilio del paciente** como único canal hacia el backend. La SD card es el buffer primario: graba continuamente y, al cumplirse el intervalo (default: 1 hora), el MCU levanta la radio WiFi, sube el batch acumulado y borra de la SD solo lo que el backend confirmó.

La red del paciente se configura **desde el propio dispositivo**, sin app: el Holter emite un access point temporal, el paciente se conecta con el celular y carga su SSID y contraseña en una página web servida por el microcontrolador (portal cautivo). Es el mismo mecanismo que usan Chromecast, Sonos o cualquier equipo IoT sin pantalla.

No se requiere módulo celular, plan de datos, app móvil ni BLE.

> **Hardware**: el MCU debe ser de la familia **ESP32** (WiFi + BLE integrados). El modelo concreto —XIAO ESP32-C3 / C6 / S3 u otro— se confirma en Fase 1 junto con el equipo de Biomédica. Todas las cifras de consumo de este documento son estimaciones de familia, a validar con medición real.

---

## Provisioning inicial — SoftAP + portal cautivo

### Flujo desde la perspectiva del paciente

1. El técnico entrega el chaleco y lo enciende. Sin credenciales guardadas, el dispositivo levanta un access point propio: SSID `Holter-XXXX` (los últimos 4 del serial), protegido con WPA2 y contraseña impresa en la etiqueta del equipo.
2. El paciente entra a la configuración de WiFi de su celular y se conecta a esa red.
3. El teléfono detecta el portal cautivo y **abre solo la página de configuración**. Como fallback (la detección de portal es inconsistente entre iOS y Android), la etiqueta también indica `http://192.168.4.1`.
4. La página muestra la lista de redes cercanas (`esp_wifi_scan_start`), el paciente elige la suya y escribe la contraseña.
5. El dispositivo guarda las credenciales en NVS, apaga el AP y se conecta a la red del domicilio. La página muestra el resultado antes de cerrar el AP.
6. Si la conexión falla (contraseña incorrecta, red 5 GHz no soportada), vuelve a levantar el AP y lo informa en la página.

### Implementación

ESP-IDF trae el componente `wifi_provisioning`, que soporta dos transportes intercambiables: `scheme_ble` y `scheme_softap`. **Se usa `scheme_softap`** — misma API y mismo handshake de seguridad, sobre HTTP en vez de GATT. No hay que implementar el protocolo desde cero.

Piezas involucradas:

| Componente | Rol |
|---|---|
| `esp_wifi` en modo AP | Emite `Holter-XXXX` con WPA2 |
| `esp_http_server` | Sirve la página de configuración y recibe el POST de credenciales |
| Servidor DNS wildcard | Responde toda consulta con `192.168.4.1` — es lo que dispara la detección de portal cautivo |
| NVS | Persiste las credenciales entre reinicios |

### Seguridad del provisioning

No se puede servir HTTPS válido desde `192.168.4.1` (ninguna CA firma una IP privada), así que la contraseña del WiFi del paciente viajaría en claro. Se mitiga por dos vías, ambas obligatorias:

1. **AP con WPA2 y contraseña única por dispositivo**, derivada del serial e impresa en la etiqueta. Cifra el enlace y evita que un tercero cercano se conecte al chaleco.
2. **`security2` (SRP6a) de `wifi_provisioning`**, que cifra el payload de credenciales a nivel de aplicación usando un Proof of Possession también impreso en la etiqueta. Con esto el HTTP plano deja de ser relevante.

Vale notar que el provisioning por BLE tampoco es seguro por sí solo —el pairing "Just Works" es vulnerable a MITM—, y por eso ESP-IDF aplica la misma capa `security2` en ambos transportes. SoftAP no es la opción menos segura de las dos.

### El AP no está siempre encendido

El modo AP se activa únicamente en tres casos:

- Al arrancar sin credenciales guardadas.
- Con el botón físico mantenido ~5 segundos.
- Por orden del backend (ver [Re-provisioning](#re-provisioning-entre-pacientes)).

Y se apaga solo tras 10 minutos de inactividad o al conectar exitosamente. Un chaleco emitiendo un AP durante las 24 h del estudio sería superficie de ataque y consumo de batería regalados.

---

## Dos slots de credenciales

El dispositivo guarda **dos redes** en NVS y las prueba en orden:

| Slot | Red | Ciclo de vida |
|---|---|---|
| 0 | **Red de la clínica** | Se carga una vez al dar de alta el equipo. No se borra nunca |
| 1 | **Red del paciente** | Se escribe en cada entrega, se borra en cada devolución |

Esto no es comodidad: es lo que evita perder datos. Si el WiFi del paciente falló los últimos días del estudio, quedan batches sin subir en la SD. Al volver el chaleco a la clínica, engancha solo con el slot 0 y drena la SD antes de que nadie lo reasigne. Sin el slot 0, esos datos quedarían varados en un equipo que ya no sabe conectarse a ninguna red.

---

## Ciclo de envío

### Máquina de estados

```
RECORDING → [timer 1h] → PREPARING_BATCH → WIFI_CONNECTING → SENDING → CONFIRMING → CLEANING_SD → WIFI_OFF → RECORDING
                                                   ↓ (sin red)              ↓ (fallo)
                                              WIFI_ERROR ←─────────────────┘
                                                   ↓
                                              WIFI_OFF → RECORDING  (reintento en próximo ciclo, backoff exponencial)
```

1. **RECORDING**: ECG → buffer en RAM → SD cada 4-8 seg. Radio apagada. Estado normal y continuo.
2. **PREPARING_BATCH**: lee de la SD los archivos pendientes (más antiguos primero), ya comprimidos con delta encoding.
3. **WIFI_CONNECTING**: enciende la radio, asocia y pide DHCP. Prueba slot 1, luego slot 0. ~2-5 seg.
4. **SENDING**: HTTPS POST al backend.
5. **CONFIRMING**: verifica HTTP 200 y el checksum devuelto.
6. **CLEANING_SD**: borra de la SD **solo** los archivos confirmados.
7. **WIFI_OFF**: apaga la radio (modem sleep / deep sleep de la radio).
8. **WIFI_ERROR**: fuera de alcance, router caído o fallo de envío → los datos quedan en la SD, reintento en el próximo ciclo. Backoff exponencial tras 3 fallos consecutivos (1h → 2h → 4h) para no drenar batería intentando conectar a una red que no está.

### Endpoint y payload

Sin cambios respecto del diseño original: el transporte cambia, el contrato con el backend no.

```
POST /devices/{device_id}/ecg-batch
Header: X-API-Key: <device-api-key>
```

Ver el detalle en [Cloud y Dashboard](04-cloud.md#recepción-directa-desde-holter).

### Idempotencia

El dispositivo va a reintentar tras cortes de red, así que el par `(device_id, batch_timestamp)` debe tener unique constraint en la base. Un reintento sobre un batch ya recibido devuelve 200 sin duplicar la fila.

---

## Re-provisioning entre pacientes

La identidad del dispositivo (serial + API key) es **permanente**. Lo que rota entre pacientes son las credenciales del slot 1 y la asignación en el backend.

### Secuencia correcta al devolver el chaleco

1. El paciente devuelve el equipo. Al entrar en la clínica, conecta solo por el **slot 0**.
2. **Drena la SD**: sube todos los batches pendientes. El portal muestra el estudio completo.
3. Recién ahí se desasigna: `patient_id = NULL`, `status = AVAILABLE`, se borra el slot 1 y se formatea la SD.
4. Se entrega al paciente siguiente, que carga su red por el portal cautivo.

Borrar la contraseña WiFi del paciente anterior no es opcional: es un dato de su hogar en un equipo que pasa a manos de un desconocido.

### Cómo se dispara

| Vía | Cuándo | Nota |
|---|---|---|
| **Botón físico** (~5 seg) | Manual, en la entrega | Levanta el AP y sobrescribe el slot 1. No borra nada más — funciona aunque el backend esté caído |
| **Orden del backend** | Al desasignar el equipo desde el portal | El dispositivo recibe `unprovision` en la respuesta de su ciclo horario y actúa |
| **Al asignar paciente nuevo** | Automático | Evita que el operador tenga que acordarse de un paso extra |

El borrado total de fábrica (incluida la API key y el slot 0) se reserva a servicio técnico — combinación de botones o mantenido 30 seg. Un paciente apretando cosas no debería poder dejar el equipo inutilizable.

### Guarda contra pérdida de datos

La orden `unprovision` es **rechazable por el firmware**: si quedan batches sin subir, el dispositivo responde con la cantidad pendiente en vez de obedecer, y el portal se lo muestra al técnico como advertencia antes de permitir la reasignación. Es la única operación irreversible de todo el flujo y merece la guarda.

---

## Limitaciones aceptadas

- **Cobertura limitada al domicilio**: fuera del alcance del router no hay transmisión. Los datos se acumulan en SD y suben al volver. Ver el dimensionamiento de SD en [Batería y datos](05-bateria-y-datos.md).
- **El paciente necesita tener WiFi**: es un criterio de inclusión del trial. Los pacientes sin WiFi en el domicilio no pueden participar con esta arquitectura.
- **Un paso de configuración a cargo del paciente**: mitigado porque el técnico puede hacerlo en la entrega, con el celular del paciente, antes de que se vaya de la clínica.
- **Solo 2.4 GHz**: la familia ESP32 no asocia a redes de 5 GHz. En routers con SSID unificado de doble banda esto normalmente funciona, pero es una causa de falla a documentar en el instructivo de soporte.
- **Detección de portal cautivo inconsistente**: iOS abre un WebView recortado, Android suele pedir confirmación de "red sin internet". Por eso la página es HTML plano con CSS inline y JS mínimo, y la IP directa figura en la etiqueta.
