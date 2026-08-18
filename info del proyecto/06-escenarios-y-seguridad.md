# Escenarios críticos, seguridad y regulatorio

## Escenarios de operación

| Escenario | Comportamiento |
|---|---|
| **Operación normal** | El nRF52840 graba continuamente al buffer → cada 1h alimenta el co-procesador WiFi y envía el batch → backend confirma → se liberan del buffer los datos enviados |
| Paciente fuera del domicilio | El buffer sigue acumulando (~40,5 MB/día). Al volver, envía batches pendientes (más antiguos primero). **Con la flash actual de 16 MB la ventana es de ~10 h; más allá de eso se pierde señal** |
| Router caído / sin internet | Igual que fuera del domicilio. Reintento en el próximo ciclo |
| Contraseña de WiFi cambiada durante el estudio | El dispositivo no asocia y acumula en el buffer. El watchdog lo detecta por ausencia de sincronización → soporte contacta al paciente para repetir el provisioning por el portal cautivo |
| Fallo de envío | Los datos permanecen en el buffer. Reintento en próximo ciclo. Backoff exponencial tras 3 fallos (1h→2h→4h) |
| Buffer lleno | Rotación FIFO: se pisan las tramas más antiguas no confirmadas y el firmware lo reporta con `STATUS_FLAG_BACKLOG_OVERFLOW`. Prioriza envío inmediato si hay red. **Con la flash actual de 16 MB esto se activa tras ~10 h sin conexión**; con la microSD pendiente, no debería activarse nunca |
| Batería baja Holter | Campo `battery_pct` en el próximo batch → alerta en dashboard médico |
| Señal WiFi débil en la habitación | Campo `wifi_rssi` en cada batch → el Tablero de Salud del Hardware lo muestra; soporte puede sugerir reubicar el router o la zona de carga nocturna |
| Anomalía detectada | Backend la detecta en el batch y notifica al médico vía dashboard. Delay máximo de 1h estando el paciente en el domicilio (aceptable para monitoreo preventivo) |
| Devolución del equipo | Conecta por el slot de la clínica → drena el buffer → recién entonces se desasigna, se borra la red del paciente y se limpia el buffer |

### Principio de diseño

El **almacenamiento local es el buffer primario y el seguro final**. Sin importar el estado de la red, el Holter siempre graba. La transmisión puede fallar sin que se pierda señal, **mientras el buffer alcance**.

> **Y hoy no alcanza.** El hardware tiene una flash SPI de **16 MB = 9,94 horas**, no una microSD de varios GB. Ese es el riesgo abierto más grande del sistema y es independiente del canal de comunicación elegido: con 10 horas de margen, un paciente que sale a trabajar un día completo pierde señal. **Agregar una microSD de 4-8 GB** lleva la ventana a ~4 meses y es el requerimiento número uno hacia el equipo de Biomédica. Ver [Batería y datos](05-bateria-y-datos.md).

Corolario operativo: **nunca se libera del buffer nada que el backend no haya confirmado**, y la orden de desasignar un equipo es rechazable por el firmware si quedan batches pendientes. Ver [Re-provisioning](07-wifi-y-provisioning.md#re-provisioning-entre-pacientes).

---

## Seguridad por capa

### WiFi (Holter → Cloud)
- TLS 1.2+ obligatorio (mbedTLS con aceleración por hardware, en el co-procesador ESP32-C3)
- Certificado del servidor validado contra CA root embebida en el firmware
- API key única por dispositivo en header `X-API-Key` (grabada en flash durante la manufactura)
- Sin flujo JWT/login — autenticación stateless por request
- Co-procesador **sin alimentación** fuera del ciclo de envío: reduce la superficie de ataque a ~8 segundos por hora

### Provisioning (SoftAP + portal cautivo)

Es la superficie nueva que introduce esta arquitectura y la que más atención requiere, porque en ese momento el dispositivo maneja un dato sensible del paciente: la contraseña de su red doméstica.

- **AP con WPA2 y contraseña única por dispositivo**, derivada del serial e impresa en la etiqueta. Cifra el enlace y evita que un tercero cercano se asocie al chaleco
- **`security2` (SRP6a) de ESP-IDF `wifi_provisioning`**: cifra el payload de credenciales a nivel de aplicación con un Proof of Possession impreso en la etiqueta. Esto es lo que compensa la imposibilidad de servir HTTPS válido desde `192.168.4.1`
- **AP efímero**: se levanta solo cuando corresponde y se apaga a los 10 minutos de inactividad o al conectar
- **Borrado de credenciales en la devolución**: la contraseña del WiFi del paciente anterior se elimina de NVS antes de reasignar el equipo

### Datos en tránsito (Dashboard médico ↔ Cloud)
- TLS 1.3 para todas las comunicaciones HTTPS

### Datos en reposo
- **Cloud**: AES-256 en PostgreSQL y S3
- **Buffer local (flash SPI)**: datos sin encriptar (tradeoff de rendimiento). El dispositivo está en poder del paciente durante el estudio. Si ANMAT lo requiere, se puede agregar encriptación AES por trama
- **Credenciales WiFi**: viven en la NVS del **co-procesador ESP32-C3**, con `nvs_flash` encriptado (flash encryption de ESP-IDF). No pasan por el nRF52840

---

## Cumplimiento regulatorio

| Regulación | Alcance | Estado |
|---|---|---|
| **Ley 25.326** (Argentina) | Protección de Datos Personales. **Incluye la contraseña del WiFi del domicilio**, que es dato personal del paciente y debe borrarse al finalizar el estudio | Requerido |
| **ANMAT** | Producto médico clase II — documentar el protocolo de comunicación WiFi, el mecanismo de provisioning y el flujo de datos como parte del expediente técnico | Requerido |
| **HIPAA** | Estándar de salud de EE.UU. | Considerar para expansión futura |

### Consentimiento informado

Además de lo clínico, el consentimiento debe informar que el equipo **usa la conexión a internet del domicilio del paciente** (~1,34 GB/mes en un estudio de ECG) y que sus credenciales de red se almacenan en el dispositivo durante el estudio y se borran al devolverlo.

---

## Verificación y testing

1. **Ciclo WiFi completo**: el buffer graba → envía → backend confirma → se libera el buffer. Probar con el router apagándose de forma intermitente — los datos deben acumularse y enviarse al recuperar la red
2. **Provisioning en iOS y Android**: verificar la detección de portal cautivo en ambos sistemas y que el fallback por IP directa (`192.168.4.1`) funciona cuando el portal no se abre solo
3. **Credenciales inválidas**: contraseña incorrecta y red de 5 GHz — el dispositivo debe volver a modo AP e informarlo en la página, no quedar colgado
4. **Backoff exponencial**: simular 3 fallos consecutivos de envío, verificar que el intervalo crece (1h→2h→4h)
5. **Acumulación en el buffer**: dejar el dispositivo fuera de alcance del router por X horas, verificar que al volver se envían todos los batches pendientes en orden, que el encadenamiento respeta el tope de ciclos consecutivos, y que al superar la capacidad del buffer se levanta `STATUS_FLAG_BACKLOG_OVERFLOW`
6. **Fallback al slot de la clínica**: con credenciales de paciente inválidas, verificar que el equipo asocia a la red de la clínica al volver
7. **Re-provisioning**: verificar que la orden de desasignar se rechaza con batches pendientes, y que tras drenar el buffer el borrado de credenciales es efectivo
8. **Cloud**: test de carga con datos simulados de múltiples dispositivos simultáneos
9. **End-to-end**: señal de generador → AFE → nRF52840 → buffer → co-procesador → WiFi → Cloud → Dashboard médico
10. **Autonomía real**: medición de consumo en las tres fases (grabación, envío, provisioning) para validar las estimaciones de [Batería y datos](05-bateria-y-datos.md). **Es la medición más importante que falta**: el consumo del nRF52840 grabando es el 96% del presupuesto energético y hoy es una estimación
