# Batería, almacenamiento y tiempos de transferencia

> **Base de estas cifras.** Los volúmenes de datos y el ratio de compresión son **mediciones** del firmware del equipo de Biomédica (`Holter-ECG-System`, `DATAFLOW.md` §9.1), no estimaciones. Los consumos de corriente **sí** son estimaciones a partir de datasheets y de la carga de trabajo, y están marcados como tales — validarlos con medición real es la tarea número uno de la Fase 1. El desarrollo completo, incluida la comparación con las alternativas BLE y SIM, está en [09-comparativa-canales-de-transmision.md](09-comparativa-canales-de-transmision.md).

## Consumo energético del sistema

| Componente | Consumo promedio | Notas |
|---|---|---|
| **MCU nRF52840** a 64 MHz, con filtros + detección de QRS a 500 Hz | **3,5 - 6 mA** | Siempre encendido. **Término dominante.** El FIR de 161 taps son 80.500 MAC/s: carga baja para un Cortex-M4F con FPU, el CPU duerme la mayor parte del tiempo |
| AFE ADS1292R, 2 canales + RLD | 0,8 mA | Siempre encendido. 335 µA/canal a 3 V (datasheet TI SBAS502C) |
| Flash SPI S25FL128L | 0,15 mA | 1,83 escrituras de página/s + un borrado de sector cada 8,7 s |
| LDO AP2112 en reposo + divisor de medición de batería | 0,1 mA | |
| **Consumo base, sin transmitir nada** | **≈ 6 mA** | |
| Co-procesador WiFi ESP32-C3 (durante el ciclo de envío) | ~120 mA × 8 s/hora | Alimentación **cortada** el resto del tiempo |
| **Total promedio** | **≈ 6,26 mA** | **El WiFi agrega solo 0,26 mA — un 4%** |

### El canal WiFi es casi gratis; el MCU que graba, no

Conviene explicitarlo porque es contraintuitivo: el equipo transmite unos **90 segundos por día** y graba **86.400**. Por eso el consumo lo define el microcontrolador que graba, no la radio que transmite.

```
Energía diaria del canal WiFi (estudio de ECG, envíos cada 1 h):
  Transmisión = 44,6 MB ÷ 500 KB/s = 89,1 s  → 120 mA × 89,1 s  = 2,97 mAh/día
  24 enganches (asociación + DHCP + TLS, ~4 s) → 24 × 0,133 mAh = 3,20 mAh/día
                                                                  ─────────────
  Total del canal = 6,17 mAh/día ÷ 24 h                          = 0,26 mA
```

Ésta es la razón por la que **no se cambia el microcontrolador**. Un ESP32 grabando de forma continua consume entre 8 y 15 mA contra los 3,5-6 mA del nRF52840; adoptarlo para tener el WiFi "integrado" bajaría la autonomía de **10,2 a 4,8 días**. Un co-procesador ESP32-C3 de ~USD 2,60 evita eso por completo y además conserva el firmware ya validado.

## Batería

La batería del diseño de Biomédica es una **Li-Po de 3,7 V y 1800 mAh** (`Datasheets/Bateria Descripcion.jpeg` del repo de firmware). Aplicando un derating del 85% —no se descarga una Li-Po hasta el 0%, y la capacidad cae con la temperatura y el envejecimiento— quedan **1530 mAh útiles**.

| Batería | Capacidad útil (85%) | Autonomía a 6,26 mA | Recomendación |
|---|---|---|---|
| 500 mAh | 425 mAh | ~68 h (~2,8 días) | Insuficiente |
| 800 mAh | 680 mAh | ~109 h (~4,5 días) | Mínimo viable |
| 1200 mAh | 1020 mAh | ~163 h (~6,8 días) | Aceptable |
| **1800 mAh** | **1530 mAh** | **~244 h (~10,2 días)** | **La del diseño actual** |

> **Techo de autonomía: 10,6 días.** Es lo que duraría el equipo sin transmitir nada. El canal WiFi se lleva 0,4 de esos días.

Diez días de autonomía cambian la operación del estudio: un Holter de 24-48 h no necesita que el paciente cargue nada, y hasta un estudio de una semana entra en una sola carga.

### Optimizaciones, en orden de impacto

1. **Bajar el consumo del nRF52840 grabando.** Es el 96% del presupuesto. Todo lo demás es ruido al lado de esto: asegurar que el CPU entre en `System ON idle` entre muestras, que el DCDC esté activo (no el LDO interno), y revisar el consumo residual de los componentes de la propia placa XIAO (LED de alimentación, chip de carga), que puede sumar cientos de µA si no se desactiva.
2. **Cortar la alimentación del co-procesador, no dormirlo.** Con un load switch el consumo entre envíos es de fugas (~1 µA). Un ESP32 en modem sleep asociado a la red seguiría escuchando beacons y costaría del orden de mA de forma permanente — un gasto que esta arquitectura no necesita pagar, porque solo transmite una vez por hora.
3. **Espaciar los envíos hasta donde lo permita el buffer.** Ver la tabla de la sección siguiente: pasar de 1 h a 4 h ahorra 0,10 mA. Es poco, pero es gratis.

---

## Volúmenes de datos

**El ECG y la impedancia son estudios separados**, no simultáneos, así que las cuentas van por separado.

### Cuánto genera el equipo

El firmware comprime **sin pérdida** con un codec Rice de predictor de orden 2, antes de escribir al buffer. El ratio está medido corriendo el codec real contra PhysioNet, incluido MIT-BIH Noise Stress Test — ruido ambulatorio real de paciente en movimiento, que es el caso que manda para dimensionar:

**Ratio medido: 12,80×** sobre ruido real (14,55× sobre señal limpia; se usa siempre el peor caso).

| Estudio | B/s comprimidos | MB/hora | **MB/día** | MB/semana |
|---|---|---|---|---|
| **ECG** (1 derivación, 24 h) | 468,6 | 1,687 | **40,5** | 283 |
| **Impedancia** (24 h continuas) | 468,6 | 1,687 | **40,5** | 283 |
| **Impedancia** (ventana nocturna 22:00-06:00) | 468,6 | 1,687 | **13,5** | 95 |

> **A validar:** a la impedancia se le asigna el mismo caudal que al ECG como **cota superior conservadora**. La bioimpedancia es una señal mucho más suave, así que es esperable que comprima *mejor* que 12,80× — pero eso todavía no está medido, y dimensionar con el número optimista sería un error.

La ventana nocturna sale de "Programación de Ventanas Operativas" en [Requerimientos.md](../Requerimientos.md), que plantea medir impedancia de 22:00 a 06:00 para capturar datos en reposo y estirar la batería.

### Almacenamiento local — el punto abierto del diseño

> **El hardware actual NO tiene microSD.** Lleva una **flash SPI S25FL128L de 16 MB**, organizada como log circular de tramas de 256 B. Eso da **9,94 horas** de grabación con una derivación.

Esto importa mucho más de lo que parece, porque es lo que sostiene (o no) el argumento central de la arquitectura WiFi: "si el paciente sale de casa, el buffer acumula y se drena al volver".

| Escenario | Datos acumulados | ¿Entra en la flash de 16 MB? |
|---|---|---|
| Operación normal (envío cada 1 h) | 1,7 MB | Sí, con muchísimo margen |
| Una jornada laboral fuera de casa (10 h) | 17 MB | **No — se empieza a perder señal** |
| Un día completo fuera | 40 MB | **No** |
| Un fin de semana largo | 122 MB | **No** |
| Un mes sin conectar (patológico) | 1,2 GB | **No** |

**Requerimiento abierto hacia el equipo de Biomédica: agregar una microSD de 4-8 GB.** Con 8 GB la ventana pasa de ~10 horas a ~4 meses y el problema desaparece por completo. El costo de sobredimensionar es nulo (las microSD de menos de 8 GB prácticamente no se fabrican) y el beneficio es que "¿qué pasa si el paciente se va el fin de semana?" deja de tener una respuesta preocupante.

Mientras tanto, la rotación FIFO del log circular pisa las tramas más antiguas no confirmadas y el firmware lo reporta con `STATUS_FLAG_BACKLOG_OVERFLOW` — así que la pérdida siempre es visible, pero es pérdida igual.

---

## Tiempos de transferencia

### WiFi: Holter → Cloud

Los datos comprimidos se envían automáticamente cada hora y se liberan del buffer tras confirmación:

| Dato | Estudio de ECG | Estudio de impedancia (nocturno) |
|---|---|---|
| Datos por batch (1 hora comprimido) | 1,687 MB | 1,687 MB |
| Con overhead HTTP + TLS (~10%) | 1,856 MB | 1,856 MB |
| Asociación WiFi + DHCP + handshake TLS | ~4 seg | ~4 seg |
| Transmisión del batch (a ~500 KB/s) | ~3,7 seg | ~3,7 seg |
| **Ciclo completo con radio encendida** | **~8 seg** | **~8 seg** |
| Ciclos por día | 24 | 8 |
| **Datos por día** (con overhead) | **44,6 MB** | **14,9 MB** |
| **Datos por mes** | **1,34 GB** | **0,45 GB** |

Los 500 KB/s son un valor prudente para HTTPS sobre un ESP32; el rango observado va de 300 KB/s a 1 MB/s.

### Cada cuánto conviene enviar

La energía de transmisión no depende de cómo se agrupen los datos (son los mismos bytes), pero la de enganche sí, porque se paga una vez por ciclo. Entonces conviene el batch más grande que el buffer tolere:

| Intervalo | Ciclos/día | Consumo del canal | Autonomía | Datos en riesgo si falla un envío |
|---|---|---|---|---|
| 15 min | 96 | 0,66 mA | 9,6 días | 0,4 MB |
| 30 min | 48 | 0,39 mA | 10,0 días | 0,8 MB |
| **1 hora** | **24** | **0,26 mA** | **10,2 días** | **1,7 MB** |
| **4 horas** | **6** | **0,16 mA** | **10,4 días** | **6,7 MB** |
| 12 horas | 2 | 0,13 mA | 10,4 días | 20,2 MB |

**El punto dulce está entre 1 y 4 horas, y el límite no lo pone la energía sino el buffer.** Con la flash actual de 9,94 horas, un intervalo de 12 horas no deja margen de reintento: si un envío falla, el siguiente batch ya no entra en memoria. Con 4 horas quedan casi 6 horas de colchón.

**Configuración recomendada: 1 hora**, con la opción de pasar a 4 si se necesita estirar la batería y el buffer ya está dimensionado.

### Impacto en la conexión del paciente

**Costo operativo del proyecto: $0** — no hay plan de datos IoT. El tráfico corre por la conexión domiciliaria del paciente: **~44,6 MB/día, ~1,34 GB/mes** para un estudio de ECG. Sobre una conexión de banda ancha típica es despreciable, y los bursts de 8 segundos por hora no compiten de forma perceptible con el uso normal del hogar.

Aun así, corresponde **informarlo explícitamente en el consentimiento del paciente**: el equipo usa su internet y su electricidad. Es transparencia básica y evita fricción si el paciente tiene una conexión medida o un plan con límite de datos.

Para dimensionar la diferencia: la [opción celular descartada](08-sim-celular-descartado.md) implicaría pagar ese mismo 1,34 GB/mes a tarifa de plan de datos argentino, unos **$2.000-3.000 ARS por equipo y por mes**.

### Recuperación tras una ausencia prolongada

Al volver el paciente al domicilio, el dispositivo envía los batches pendientes del más antiguo al más nuevo. Conviene que el firmware, al detectar backlog, **encadene envíos** en lugar de esperar una hora entre cada uno — pero con un tope de ciclos consecutivos, para no vaciar la batería de golpe si el backlog es grande.

Con el buffer actual de 16 MB el backlog máximo posible es de ~10 horas (17 MB), que se drena en unos 40 segundos de radio encendida. Con la microSD pendiente, un fin de semana acumulado (~122 MB) se drenaría en unos 5 minutos repartidos en ciclos sucesivos.
