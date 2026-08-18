# Comparativa de canales de transmisión: chaleco → backend

> **Qué es este documento.** Una comparación desde cero de las tres formas posibles de mandar los datos del chaleco a la base de datos: **BLE con app propia**, **WiFi directo** y **SIM celular directo**. Se hizo ignorando a propósito la decisión ya registrada en [01-justificacion.md](01-justificacion.md), para poder revisarla con números propios.
>
> Las prioridades del proyecto, en orden: **(1) duración de la batería del chaleco**, **(2) facilidad de uso para pacientes de 40-70 años**, y **(3)** que la arquitectura sirva también para el producto real, sin tener que rehacerla después.
>
> El cuerpo del documento se puede leer sin mirar las cuentas. Las cuentas están para que cualquiera pueda rehacerlas y discutirlas.

---

## Resumen en una pantalla

| | A — BLE + app | B — WiFi directo | C — SIM directo |
|---|---|---|---|
| **Autonomía (estudio ECG)** | **10,0 días** | **10,2 días** (co-procesador)<br>4,8 días (cambiando el MCU) | 9,1 / **6,8** / 3,3 días<br>(cobertura buena / típica / pobre) |
| **Costo extra de hardware** | **USD 0** | USD 2,60 - 3,10 | USD 10,70 - 13,20 |
| **Costo recurrente** | USD 0 | USD 0 | ~$2.000-3.000 ARS por equipo/mes |
| **Qué tiene que hacer el paciente** | Tener el celular cerca y la app viva, **todos los días** | Configurar el WiFi **una vez**, en la entrega | **Nada** |

**Recomendación:** WiFi como canal principal, implementado con un **co-procesador WiFi de USD 2,60 al lado del nRF52840 actual** (no cambiando el microcontrolador), BLE reubicado como canal de servicio y provisioning, y el lugar para el módulo SIM previsto en la placa pero sin montar. El detalle está en la [conclusión](#6-conclusión--qué-haría-yo).

---

## 1. Punto de partida: qué hay hoy

Antes de comparar hace falta fijar contra qué se compara. Estos son los datos del hardware **real**, sacados del repositorio de firmware del equipo de Biomédica (`Holter-ECG-System`), no de estimaciones:

| Pieza | Qué es | Dato relevante |
|---|---|---|
| **Microcontrolador** | Seeed XIAO nRF52840 | Cortex-M4F + **BLE**. **No tiene WiFi.** |
| **Front-end analógico** | TI ADS1292R | 2 canales de 24 bits, muestreo a **500 Hz** |
| **Memoria local** | Flash SPI S25FL128L | **16 MB** → **9,94 horas** de buffer |
| **Batería** | Li-Po 3,7 V | **1800 mAh** (`Datasheets/Bateria Descripcion.jpeg`) |

Dos aclaraciones que importan para todo lo que sigue:

- **La placa actual no tiene WiFi.** Esto es lo que hace que la opción B no sea gratis, al contrario de lo que dice hoy [01-justificacion.md](01-justificacion.md). Se desarrolla en la sección 3.
- **El buffer local es de 10 horas, no de meses.** La microSD de 4-8 GB que describen los documentos actuales todavía no existe en el hardware. Esto limita cuán espaciados pueden ser los envíos, y aparece como restricción real en el cálculo del batch óptimo.

### 1.1 Cuántos datos se generan

El firmware comprime la señal **sin pérdida** (codec Rice con predictor de orden 2) antes de guardarla. El ratio no es una estimación: está medido corriendo el codec real contra las bases de PhysioNet, incluyendo MIT-BIH Noise Stress Test, que es ruido ambulatorio real de un paciente en movimiento — el caso que manda para dimensionar.

**Ratio medido: 12,80× sobre ruido real** (14,55× sobre señal limpia; se usa siempre el peor caso).

Como los estudios de ECG y de impedancia **no se hacen al mismo tiempo**, las cuentas van por separado:

| Estudio | Canales | B/s comprimidos | MB/hora | **MB/día** |
|---|---|---|---|---|
| **ECG** (1 derivación, 24 h) | 1 | 468,6 | 1,687 | **40,5** |
| **Impedancia** (24 h continuas) | 1 | 468,6 | 1,687 | **40,5** |
| **Impedancia** (ventana nocturna 22:00-06:00) | 1 | 468,6 | 1,687 | **13,5** |

Dos notas sobre la fila de impedancia:

- Se le asigna el mismo caudal que al ECG como **cota superior conservadora**. La bioimpedancia es una señal mucho más suave que el ECG, así que es esperable que el codec la comprima *mejor* que 12,80× — pero eso todavía no está medido, y dimensionar con el número optimista sería un error.
- La ventana nocturna sale de "Programación de Ventanas Operativas" en [Requerimientos.md](../Requerimientos.md), que plantea explícitamente medir impedancia de 22:00 a 06:00 para capturar datos en reposo y estirar la batería.

A todo esto hay que sumarle el **overhead de protocolo**: los encabezados de HTTP, el handshake y el registro de TLS. Se usa **+10%** para WiFi y SIM, y **+4%** para BLE (que es un protocolo más liviano en encabezados). Entonces, para un estudio de ECG se transmiten **44,6 MB/día** reales.

### 1.2 Cuánto consume el chaleco sin transmitir nada

Éste es el número más importante del documento, porque es el denominador de todo lo demás: si el equipo ya consume mucho grabando, el canal de transmisión casi no importa; si consume poco, el canal pasa a ser decisivo.

| Bloque | Corriente media | De dónde sale |
|---|---|---|
| nRF52840 a 64 MHz, con el pipeline de filtros y detección de QRS corriendo a 500 Hz, DCDC activo | 3,5 - 6 mA | El FIR de 161 taps son 80.500 multiplicaciones-acumulaciones por segundo: para un Cortex-M4F con FPU es carga baja, el CPU duerme la mayor parte del tiempo |
| ADS1292R, 2 canales + circuito de RLD | 0,8 mA | 335 µA por canal a 3 V (datasheet TI SBAS502C) |
| Flash S25FL128L: 1,83 escrituras de página/s + un borrado de sector cada 8,7 s | 0,15 mA | |
| LDO AP2112 en reposo + divisor resistivo de medición de batería | 0,1 mA | |
| **Consumo base, sin radio** | **≈ 6 mA** | |

**Batería útil:** 1800 mAh × 0,85 de derating (no se descarga una Li-Po hasta el 0%, y la capacidad cae con la temperatura y el envejecimiento) = **1530 mAh**.

> ### Techo de autonomía: 1530 mAh ÷ 6 mA = 255 h = **10,6 días**
>
> Ningún canal de transmisión puede superar esto. La pregunta de todo el documento es **cuántos de esos 10,6 días se come cada opción.**

Vale marcar que este número es entre 3 y 5 veces mejor que los "2-3 días" que estiman hoy los documentos del proyecto, y la razón es simple: esos documentos asumen un ESP32, que consume entre 8 y 20 mA solo grabando. El nRF52840 es un microcontrolador mucho más eficiente. Volveremos sobre esto en la sección 3.

---

## 2. Opción A — BLE al celular, y del celular al backend

**Cómo funciona.** El chaleco habla por Bluetooth de baja energía con una app propia instalada en el celular del paciente. La app recibe los datos y los reenvía al backend por internet (WiFi o datos móviles del paciente).

### 2.1 Modelo de energía

BLE es, de lejos, la radio más barata de las tres. Su costo se descompone en dos partes:

1. **Mantener el enlace vivo** aunque no se transmita nada: con un intervalo de conexión de 30 ms, el chaleco despierta la radio brevemente cada 30 ms para decir "sigo acá". Cuesta ≈ **0,2 mA**.
2. **Transmitir**: durante la transferencia, radio + CPU consumen ≈ **7 mA**.

El throughput efectivo se toma en **20 KB/s**, que es deliberadamente conservador: el firmware ya limita el drenaje a 12,5 KB/s en régimen relajado y 62,5 KB/s en modo catch-up, y un enlace BLE con MTU de 247 bytes e intervalo de 30 ms da margen de sobra. Se usa 20 KB/s para no depender de que el stack del teléfono coopere.

### 2.2 Cuentas — estudio de ECG

```
Datos a transmitir     = 40,5 MB/día × 1,04 (overhead BLE)  = 42,1 MB/día
Tiempo de radio activa = 42,1e6 B ÷ 20e3 B/s                = 2.105 s/día  (35 min/día)
Energía de transmisión = 7 mA × 2.105 s = 14.735 mA·s       = 4,09 mAh/día
Consumo medio de TX    = 4,09 mAh ÷ 24 h                    = 0,17 mA
Mantenimiento del enlace                                     = 0,20 mA
                                                            ─────────────
Costo total del canal BLE                                   = 0,37 mA  (+6,2%)

Consumo total del sistema = 6,0 + 0,37                      = 6,37 mA
Autonomía = 1530 mAh ÷ 6,37 mA = 240 h                      = 10,0 días
```

### 2.3 Cuentas — estudio de impedancia (ventana nocturna)

```
Datos a transmitir     = 13,5 MB/día × 1,04                 = 14,0 MB/día
Tiempo de radio activa = 14,0e6 ÷ 20e3                      = 702 s/día
Energía de transmisión = 7 mA × 702 s = 4.914 mA·s          = 1,37 mAh/día  → 0,06 mA
Mantenimiento del enlace                                     = 0,20 mA
Consumo total del sistema = 6,0 + 0,26                      = 6,26 mA
Autonomía = 1530 ÷ 6,26 = 244 h                             = 10,2 días
```

### 2.4 Batch óptimo

**Con BLE el tamaño del batch casi no cambia nada**, y conviene entender por qué, porque en las otras dos opciones sí importa muchísimo.

En BLE no hay un costo fijo de "arrancar la conexión": el enlace ya está establecido y se mantiene con esos 0,2 mA. Mandar 40 MB en 24 tandas de 1,7 MB cuesta prácticamente lo mismo que mandarlos en un goteo continuo, porque el gasto es proporcional a los bytes movidos y nada más.

Esto tiene una consecuencia práctica agradable: **con BLE conviene transmitir de forma continua o casi**, que además es lo que minimiza la latencia y lo que ya hace el firmware actual. No hay que optimizar nada.

### 2.5 Costo

| Concepto | Volumen (USD/equipo) | Prototipo en Argentina |
|---|---|---|
| Hardware adicional | **USD 0** — la radio BLE ya está dentro del nRF52840 | $0 |
| Licencia Apple Developer | USD 99/año (toda la flota) | — |
| Google Play Console | USD 25, pago único | — |

**Pero el costo real de esta opción no es hardware, es software.** Requiere desarrollar y mantener una app para iOS y otra para Android, con transferencia de datos en segundo plano, reconexión automática, manejo de permisos y publicación en dos tiendas. Es un cuarto componente del sistema con su propio ciclo de bugs y testing, y con las tiendas de aplicaciones como dependencia externa permanente.

### 2.6 Ventajas y desventajas

**A favor:**
- Costo de hardware cero y consumo casi cero: solo 6% de la autonomía.
- Latencia mínima (segundos, no una hora), lo que habilitaría alertas casi en tiempo real.
- El firmware **ya está escrito y validado**: el servicio BLE existe, con emparejamiento autenticado por passkey de 6 dígitos y confirmación por ACK de cada trama.
- No consume datos ni internet del hogar del paciente; usa el plan del celular.

**En contra — y esto es lo que la descarta como canal principal:**
- **Depende del comportamiento del paciente, todos los días.** Si deja el celular en otra habitación, si se le agota la batería del teléfono, si cierra la app, o si iOS decide suspenderla en segundo plano, los datos dejan de llegar. Para un paciente de 40-70 años que no eligió ser usuario de una app, esa variabilidad es el peor perfil de los tres.
- Requiere que el paciente tenga un smartphone compatible y sepa usarlo. Es un criterio de inclusión del trial más restrictivo que tener WiFi.
- Suma dos aplicaciones al alcance del TFG.
- El teléfono se convierte en un punto de falla intermedio: un dato puede estar en el chaleco, en el celular, o en el backend, y hay que saber cuál en cada momento.

---

## 3. Opción B — WiFi directo al backend

**Cómo funciona.** El chaleco se conecta al router del domicilio del paciente y sube los datos al backend por HTTPS, sin celular ni app en el medio. El paciente configura su red una sola vez, en la entrega.

### 3.1 El problema previo: la placa actual no tiene WiFi

El nRF52840 tiene BLE pero no tiene WiFi. Entonces esta opción se abre en dos caminos muy distintos, y la diferencia entre ellos es la más grande de todo el documento:

- **B1 — Cambiar el microcontrolador por un ESP32**, que trae WiFi integrado. Es lo que asumen hoy los documentos del proyecto.
- **B2 — Dejar el nRF52840 donde está y agregarle un co-procesador WiFi**: un módulo ESP32-C3 conectado por UART, que se enciende solo para enviar y se apaga el resto del tiempo. El nRF52840 sigue haciendo todo lo demás.

**El costo energético de cada camino es completamente distinto**, así que van por separado.

### 3.2 Modelo de energía

Con WiFi aparece algo que en BLE no existía: un **costo fijo por cada ciclo de envío**. Antes de mandar el primer byte hay que encender la radio, asociarse al router, pedir una IP por DHCP y hacer el handshake de TLS. Eso son unos 4 segundos con la radio consumiendo ~120 mA, se manden 100 KB o 10 MB.

```
Costo fijo por ciclo = 120 mA × 4 s = 480 mA·s = 0,133 mAh
Costo por byte       = 120 mA, a un throughput HTTPS de 500 KB/s
```

Los 500 KB/s son un valor de trabajo prudente para HTTPS sobre un ESP32 (el rango observado va de 300 KB/s a 1 MB/s).

### 3.3 Cuentas — estudio de ECG, camino B2 (co-procesador)

```
Datos a transmitir        = 40,5 MB/día × 1,10 (overhead HTTP+TLS) = 44,6 MB/día
Tiempo de radio en TX     = 44,6e6 B ÷ 500e3 B/s                   = 89,1 s/día
Energía de transmisión    = 120 mA × 89,1 s = 10.692 mA·s          = 2,97 mAh/día
Energía de los 24 enganches = 24 × 0,133 mAh                       = 3,20 mAh/día
                                                                   ─────────────
Energía diaria del canal WiFi                                      = 6,17 mAh/día
Consumo medio del canal   = 6,17 ÷ 24                              = 0,26 mA  (+4,3%)

Consumo total del sistema = 6,0 (base, sin cambios) + 0,26         = 6,26 mA
Autonomía = 1530 mAh ÷ 6,26 mA = 244 h                             = 10,2 días
```

El consumo base **no cambia** respecto del punto de partida, porque el nRF52840 sigue siendo el que graba y el co-procesador está apagado (cortado por un load switch, no en modo bajo consumo) durante los 23,97 minutos de cada 24 horas en que no se usa.

### 3.4 Cuentas — estudio de ECG, camino B1 (cambiar el MCU a ESP32)

Acá lo que cambia no es la radio: es el **consumo base**. Un ESP32-C6 grabando de forma continua, incluso con light sleep bien implementado entre escrituras, consume entre 8 y 15 mA — contra los 3,5-6 mA del nRF52840. Se usa 12 mA como valor de trabajo.

```
Consumo base con ESP32-C6 = 12 (MCU) + 0,8 (AFE) + 0,15 (flash) + 0,1 (LDO) = 13,05 mA
Costo del canal WiFi (idéntico al de B2)                                     =  0,26 mA
                                                                             ──────────
Consumo total                                                                = 13,3 mA
Autonomía = 1530 mAh ÷ 13,3 mA = 115 h                                       = 4,8 días
```

> **Éste es el hallazgo central del documento.** Los dos caminos llegan al mismo lugar funcional — WiFi directo al backend — pero uno da **10,2 días** y el otro **4,8 días**. La diferencia no la hace la radio (cuesta 0,26 mA en ambos casos), la hace **el microcontrolador que graba las 24 horas**.
>
> Dicho de otra forma: cambiar el MCU para "conseguir el WiFi gratis" cuesta **más de la mitad de la autonomía del equipo**. El co-procesador de USD 2,60 la conserva entera.

### 3.5 Cuentas — estudio de impedancia (ventana nocturna, camino B2)

```
Datos a transmitir     = 13,5 MB/día × 1,10                = 14,9 MB/día
Tiempo de radio en TX  = 14,9e6 ÷ 500e3                    = 29,7 s/día  → 0,99 mAh
Energía de los 8 enganches = 8 × 0,133 mAh                 = 1,07 mAh
Energía diaria del canal                                    = 2,06 mAh → 0,09 mA
Consumo total del sistema = 6,0 + 0,09                     = 6,09 mA
Autonomía = 1530 ÷ 6,09 = 251 h                            = 10,5 días
```

### 3.6 Batch óptimo

Acá sí hay algo que optimizar, y la estructura del problema es clara: la energía de transmisión **no depende** de cómo se agrupen los datos (son los mismos bytes), pero la energía de enganche **sí**, porque se paga una vez por ciclo. Entonces conviene el batch más grande posible.

| Intervalo | Ciclos/día | Enganches | Transmisión | Total del canal | Autonomía | Datos en riesgo si falla |
|---|---|---|---|---|---|---|
| 15 min | 96 | 12,80 mAh | 2,97 mAh | 0,66 mA | 9,6 días | 0,4 MB |
| 30 min | 48 | 6,39 mAh | 2,97 mAh | 0,39 mA | 10,0 días | 0,8 MB |
| **1 hora** | **24** | **3,20 mAh** | **2,97 mAh** | **0,26 mA** | **10,2 días** | **1,7 MB** |
| **4 horas** | **6** | **0,80 mAh** | **2,97 mAh** | **0,16 mA** | **10,4 días** | **6,7 MB** |
| 12 horas | 2 | 0,27 mAh | 2,97 mAh | 0,13 mA | 10,4 días | 20,2 MB |

**El punto dulce está entre 1 y 4 horas**, y la razón de que no sea más es interesante: **no es la energía, es el buffer**. La flash de 16 MB aguanta 9,94 horas de grabación. Un intervalo de 12 horas no tiene margen de seguridad ninguno — si un envío falla, el siguiente ya no entra en memoria y se empieza a perder señal. Con 4 horas quedan casi 6 horas de colchón para reintentar.

Y desde las 4 horas en adelante la mejora es marginal (10,4 contra 10,2 días): no vale la pena pagar riesgo por 0,2 días. **Recomendación: 1 hora, con la opción de configurarlo a 4 si se necesita estirar la batería.**

### 3.7 Costo

**Camino B2 — co-procesador (recomendado):**

| Componente | Volumen (USD/equipo, LCSC 1k+) | Prototipo en Argentina |
|---|---|---|
| ESP32-C3-MINI-1(U)-N4 | 2,12 - 2,60 | DevKit ESP32-C3/C6: ~$15.000 - 35.000 ARS |
| Load switch (ej. TPS22860) para cortarle la alimentación | 0,15 | incluido en el devkit |
| Antena chip 2,4 GHz + red de adaptación | 0,30 | incluida en el devkit |
| **Total** | **USD 2,60 - 3,10** | **~$15.000 - 35.000 ARS** |

**Camino B1 — cambiar el MCU:**

| Componente | Volumen (USD/equipo) |
|---|---|
| ESP32-C6-MINI-1-N4 (reemplaza al módulo nRF52840) | 2,74 |
| **Total en BOM** | **≈ USD 0 — es neutro, o incluso levemente más barato** |

El BOM es neutro, pero **el costo verdadero de B1 no está en el BOM**: es portar todo el firmware. El pipeline de filtros, el detector de QRS, el codec de compresión y el driver de la flash están escritos y validados sobre nRF52840, con un rendimiento medido contra MIT-BIH de **99,28% de sensibilidad y 99,73% de predictividad positiva**. Portar eso a ESP-IDF significa reescribir, revalidar contra las bases de PhysioNet y volver a correr las 9 suites de tests. Es trabajo del orden de cientos de horas, y hasta que se termine el proyecto no tiene un firmware confiable.

### 3.8 Ventajas y desventajas

**A favor:**
- **Prácticamente no cuesta batería**: 4,3% de la autonomía por el camino B2.
- **La mejor experiencia de uso sostenida en el tiempo**: el paciente configura la red una vez, en la clínica, con el técnico al lado. Después el chaleco funciona solo. No hay nada que abrir, nada que recordar, nada que mantener cargado además del propio chaleco.
- Costo recurrente **cero**: no hay plan de datos ni SIMs que administrar.
- No requiere app: la configuración se hace desde el navegador de cualquier celular (portal cautivo), sin instalar nada.
- El paciente pasa en su casa la mayor parte del estudio, incluidas las noches — que es justo cuando se mide impedancia.

**En contra:**
- **No transmite fuera del domicilio.** La grabación sigue, pero los datos se acumulan. Con el buffer actual de 16 MB eso da **9,94 horas**, no meses: un paciente que sale a trabajar un día completo puede perder señal. Esto es un requerimiento abierto hacia el equipo de Biomédica (agregar microSD), no algo resuelto.
- **Tener WiFi pasa a ser criterio de inclusión** del trial.
- Depende del router y del proveedor de internet del paciente. Si cambia la contraseña, hay que repetir la configuración.
- Solo 2,4 GHz: los módulos ESP32 no asocian a redes de 5 GHz.
- Suma un componente y un poco de área de PCB, y el firmware tiene que manejar el diálogo por UART con el co-procesador.

---

## 4. Opción C — SIM celular directo al backend

**Cómo funciona.** El chaleco lleva su propio módulo celular con una tarjeta SIM y envía los datos al backend por la red móvil, desde cualquier lugar. El paciente no configura nada y no necesita ni WiFi ni celular.

Módulo de referencia: **SIMCom SIM7080G** (LTE-M / NB-IoT), que es el candidato que ya venía evaluando el proyecto.

### 4.1 El punto que decide esta opción: la velocidad de subida

Acá hay que corregir un error que arrastra la documentación actual del proyecto. [05-bateria-y-datos.md](05-bateria-y-datos.md) estima el ciclo de envío celular en **~30 segundos por hora**. Para subir el batch de una hora en 30 segundos harían falta unos **720 kbps sostenidos de subida**, y LTE-M no llega ni cerca de eso en condiciones reales.

| Fuente | Subida |
|---|---|
| Especificación del SIM7080G | 1.119 kbps (techo teórico) |
| Típico citado para Cat-M1 | ~380 kbps |
| Observado en campo, según cobertura | 25 - 200 kbps |

LTE-M es una tecnología diseñada para **telemetría**: mandar unos pocos kilobytes de un sensor, con muy bajo consumo y excelente penetración en interiores. No está pensada para mover decenas de megabytes por día. Ese desajuste es el eje de toda esta sección.

Por eso las cuentas van con **tres escenarios de cobertura**, en vez de un número único.

### 4.2 Modelo de energía

```
Salir de PSM y engancharse a la red = 10 s × 50 mA = 500 mA·s = 0,139 mAh por ciclo
Corriente media durante la transmisión = 80 mA
   (el pico instantáneo llega a 250 mA a 23 dBm; el promedio con el ciclo
    de trabajo del uplink LTE queda en torno a 80 mA)
En reposo profundo (PSM), entre ciclos = 3 µA → despreciable
```

El PSM (Power Saving Mode) del módulo funciona muy bien y hace que el consumo *entre* envíos sea irrelevante. El problema no es el reposo: es cuánto tiempo hay que tener la radio prendida transmitiendo.

### 4.3 Cuentas — estudio de ECG

Datos a transmitir: 40,5 MB/día × 1,10 = **44,6 MB/día**.

**Escenario 1 — cobertura buena (380 kbps ≈ 47,5 KB/s)**
```
Tiempo de radio en TX  = 44,6e6 ÷ 47,5e3            = 939 s/día  (15,6 min/día)
Energía de transmisión = 80 mA × 939 s              = 20,9 mAh/día
Energía de 24 enganches = 24 × 0,139                =  3,3 mAh/día
Consumo del canal      = 24,2 mAh ÷ 24 h            =  1,01 mA   (+17%)
Total = 6,0 + 1,01 = 7,01 mA  →  1530 ÷ 7,01 = 218 h = 9,1 días
```

**Escenario 2 — cobertura típica (100 kbps = 12,5 KB/s)**
```
Tiempo de radio en TX  = 44,6e6 ÷ 12,5e3            = 3.568 s/día  (59,5 min/día)
Energía de transmisión = 80 mA × 3.568 s            = 79,3 mAh/día
Energía de 24 enganches                             =  3,3 mAh/día
Consumo del canal      = 82,6 mAh ÷ 24 h            =  3,44 mA   (+57%)
Total = 6,0 + 3,44 = 9,44 mA  →  1530 ÷ 9,44 = 162 h = 6,8 días
```

**Escenario 3 — cobertura pobre (25 kbps = 3,1 KB/s)**
```
Tiempo de radio en TX  = 44,6e6 ÷ 3,1e3             = 14.272 s/día  (¡4 horas/día!)
Energía de transmisión = 80 mA × 14.272 s           = 317,2 mAh/día
Energía de 24 enganches                             =   3,3 mAh/día
Consumo del canal      = 320,5 mAh ÷ 24 h           =  13,35 mA   (+222%)
Total = 6,0 + 13,35 = 19,35 mA  →  1530 ÷ 19,35 = 79 h = 3,3 días
```

> Lo que hay que leer en estos tres números no es el promedio, sino la **dispersión**: la autonomía del equipo pasa de 9,1 a 3,3 días según en qué parte de la casa esté el paciente. Un equipo médico cuya batería dura entre 3 y 9 días según la cobertura es un equipo del que no se le puede decir al paciente cuándo cargarlo. Y la mala cobertura en interiores — justamente el caso de uso — es la situación normal, no la excepción.

### 4.4 Cuentas — estudio de impedancia (ventana nocturna, cobertura típica)

```
Datos a transmitir     = 13,5 MB/día × 1,10         = 14,9 MB/día
Tiempo de radio en TX  = 14,9e6 ÷ 12,5e3            = 1.192 s/día
Energía de transmisión = 80 mA × 1.192 s            = 26,5 mAh/día
Energía de 8 enganches = 8 × 0,139                  =  1,1 mAh/día
Consumo del canal      = 27,6 mAh ÷ 24 h            =  1,15 mA
Total = 6,0 + 1,15 = 7,15 mA  →  1530 ÷ 7,15 = 214 h = 8,9 días
```

El estudio de impedancia nocturno es el único caso donde la SIM se comporta razonablemente, simplemente porque son tres veces menos datos.

### 4.5 Batch óptimo

**Con SIM, agrandar el batch casi no sirve**, y es el espejo exacto de lo que pasaba con WiFi.

El costo de enganche (0,139 mAh) es real, pero es **ridículamente chico comparado con el costo de transmisión** (79,3 mAh/día en cobertura típica). Pasar de 24 envíos por día a 6 ahorra 2,5 mAh de un total de 82,6 mAh: un 3%. No mueve la aguja.

**En la opción SIM el consumo está dominado por la cantidad de bytes, no por la cantidad de ciclos.** La única palanca real para bajarlo sería **transmitir menos datos** — comprimir más agresivamente (con pérdida, lo cual está descartado para un registro clínico), o mandar solo resúmenes en vez de la señal completa (lo cual rompe el propósito de un Holter).

### 4.6 Costo de hardware

| Componente | Volumen (USD/equipo) | Prototipo en Argentina |
|---|---|---|
| Módulo SIM7080G (o SIM7080G-M) | 8,63 - 11,11 | breakout importado USD 18 - 37 |
| Portasim nano push-push | 0,40 | incluido |
| Antena LTE (FPC) + red de adaptación | 0,80 | incluida |
| Capacitor de bulk baja ESR (para el pico de 250 mA) | 0,30 | — |
| Regulador conmutado 3,8 V capaz de 2 A | 0,60 | — |
| **Total** | **USD 10,70 - 13,20** | **~$90.000 - 150.000 ARS** (estimado; disponibilidad local escasa) |

A esto hay que sumarle lo que no aparece en una tabla de precios: **área de PCB, diseño de radiofrecuencia y certificación**. El pico de 250 mA no es "sumar un módulo": obliga a rediseñar el árbol de alimentación para que la batería y el regulador lo entreguen sin que caiga la tensión y se resetee el microcontrolador. Es trabajo del equipo de Biomédica que las otras dos opciones no consumen.

### 4.7 Costo recurrente: el plan de datos en Argentina

Éste es el otro punto que descoloca a la opción C.

```
Estudio de ECG:        44,6 MB/día × 30 = 1,34 GB por mes y por equipo
Estudio de impedancia:  14,9 MB/día × 30 = 0,45 GB por mes y por equipo
```

**Los planes M2M/IoT argentinos no sirven para esto.** Están dimensionados para telemetría: la oferta multicarrier típica (Movistar + Claro) ronda los **20 MB por mes**, unas 60 veces menos de lo que necesita un estudio de ECG. Los APN M2M locales (`m2m.movistar.com.ar`, `igprs.claro.com.ar`, `datos.personal.com`) existen, pero el volumen contratado es de otro orden.

Hay que ir entonces a un **plan de datos de consumidor**, que es lo mismo que decir que cada chaleco lleva algo parecido a la línea de un celular:

| Referencia (Movistar prepago, ago. 2026) | Precio |
|---|---|
| 1 GB | $1.490 ARS |
| 5 GB | $6.100 ARS |

```
Estudio de ECG (1,34 GB/mes)  →  ~$2.000 - 3.000 ARS por equipo/mes
Trial de 50 equipos            →  ~$100.000 - 150.000 ARS por mes
Estudio de impedancia (0,45 GB/mes) → ~$1.490 ARS por equipo/mes
```

Además del dinero, hay una carga administrativa permanente: dar de alta y de baja SIMs, controlar saldo, y el riesgo de que a un equipo se le agote el plan en medio de un estudio de un paciente.

### 4.8 Ventajas y desventajas

**A favor — y su ventaja es real, no menor:**
- **La mejor experiencia de uso de las tres, sin discusión.** El paciente no configura nada, no instala nada, no necesita WiFi ni smartphone. Se pone el chaleco y ya está. Para el usuario de 40-70 años que es el objetivo del producto, esto es exactamente lo que uno querría.
- **Cobertura casi total**: transmite desde la casa, la calle, el trabajo o la casa de un familiar. Es la única opción que hace monitoreo verdaderamente ambulatorio.
- Elimina de raíz el criterio de inclusión "tener WiFi", que restringe el reclutamiento del trial.
- No depende de terceros que el proyecto no controla (router del paciente, sistema operativo del teléfono).

**En contra:**
- **Es la opción que más batería consume, con mucha diferencia**, y lo peor es que consume una cantidad *impredecible*: entre 9,1 y 3,3 días según la cobertura del lugar donde esté el paciente.
- Es la más cara en hardware (4 a 5 veces el co-procesador WiFi) y **la única con costo recurrente**.
- Es la que más trabajo agrega al equipo de Biomédica: diseño de RF, árbol de alimentación para el pico de corriente, antena, certificación.
- Los planes IoT locales no cubren este volumen de datos, así que se paga tarifa de consumidor.
- LTE-M está siendo usada acá para algo para lo que no fue diseñada. No es una limitación del módulo elegido: es de la tecnología.

---

## 5. Tabla comparativa

Todas las filas de autonomía usan la misma base de 6 mA, la misma batería de 1530 mAh útiles y el mismo caudal medido. La columna de WiFi es el camino B2 (co-procesador); el camino B1 (cambiar el MCU) aparece aparte porque es la comparación que más importa.

| Criterio | A — BLE + app | **B2 — WiFi (co-procesador)** | B1 — WiFi (cambiando el MCU) | C — SIM celular |
|---|---|---|---|---|
| **Autonomía, estudio ECG** | 10,0 días | **10,2 días** | 4,8 días | 9,1 / 6,8 / 3,3 días |
| **Autonomía, estudio impedancia** | 10,2 días | **10,5 días** | 4,9 días | 8,9 días |
| **% de autonomía que se lleva el canal** | 5,7% | **4,0%** | 55% | 14% / 36% / 69% |
| **Predecibilidad del consumo** | Alta | **Alta** | Alta | **Baja** — depende de la cobertura |
| **Costo extra de hardware (volumen)** | **USD 0** | USD 2,60 - 3,10 | USD 0 (BOM neutro) | USD 10,70 - 13,20 |
| **Costo de prototipo en Argentina** | **$0** | ~$15.000 - 35.000 ARS | ~$15.000 - 35.000 ARS | ~$90.000 - 150.000 ARS |
| **Costo recurrente** | USD 0 | **USD 0** | USD 0 | ~$2.000 - 3.000 ARS/equipo/mes |
| **Impacto en el firmware existente** | **Ninguno — ya está hecho** | Bajo: agregar diálogo UART | **Port completo + revalidación** | Medio: máquina de estados AT |
| **Software adicional a construir** | App iOS **y** Android | Portal de configuración web | Portal de configuración web | **Ninguno** |
| **Trabajo extra para Biomédica** | Ninguno | Bajo (1 componente + load switch) | Cambio de MCU | **Alto**: RF, antena, árbol de alimentación para 250 mA |
| **Qué tiene que hacer el paciente** | Celular cerca y app viva, **a diario** | Configurar el WiFi **una vez** | Configurar el WiFi una vez | **Nada** |
| **Cobertura** | Donde esté el celular | Dentro del domicilio | Dentro del domicilio | **Casi total** |
| **Latencia hasta el backend** | **Segundos** | ~1 hora en casa | ~1 hora en casa | ~1 hora, en cualquier lado |
| **Puntos de falla fuera de nuestro control** | SO del teléfono, tiendas de apps, hábito del paciente | Router y proveedor del paciente | Router y proveedor del paciente | Cobertura y saldo del operador |
| **Riesgo de perder datos** | Medio: depende de la conducta del paciente | Bajo estando en casa; **alto fuera**, con el buffer actual de 10 h | Igual que B2 | **Bajo** |
| **Camino hacia producto real** | Malo como canal único | **Bueno** | Bueno, pero con menos batería | Excelente en UX, malo en batería y costo |

---

## 6. Conclusión — qué haría yo

### La recomendación

> **WiFi como canal principal, implementado con un co-procesador ESP32-C3 al lado del nRF52840 actual (camino B2).**
>
> **BLE reubicado**: no como camino de datos, sino como canal de servicio — configuración inicial del equipo y app de acompañamiento del paciente.
>
> **El módulo SIM previsto en la placa pero sin montar**: dejar el footprint del SIM7080G y el portasim dibujados en la PCB, sin poblarlos.

### Por qué, en orden de peso

**1. La batería es la prioridad número uno, y ahí el orden es contundente.**

De los 10,6 días teóricos del equipo, cada opción se lleva:

```
WiFi con co-procesador   →  0,4 días   (4,0%)
BLE                      →  0,6 días   (5,7%)
SIM, cobertura típica    →  3,8 días   (36%)
SIM, cobertura pobre     →  7,3 días   (69%)
WiFi cambiando el MCU    →  5,8 días   (55%)
```

BLE y WiFi son, para efectos prácticos, **gratis** en términos de batería. La SIM no: en el mejor caso se lleva más de un tercio de la autonomía, y en el peor deja al equipo en 3,3 días. Peor todavía, es un consumo que no se puede prometer de antemano, porque depende de la cobertura del rincón de la casa donde esté el paciente.

**2. No hay que cambiar el microcontrolador, y esto es lo más importante que salió de este análisis.**

Los documentos actuales del proyecto asumen un ESP32 porque "trae el WiFi integrado". Pero el firmware real corre sobre nRF52840, que consume 2 a 3 veces menos grabando de forma continua — y como el equipo graba las 24 horas y transmite 90 segundos por día, **el consumo lo define el microcontrolador que graba, no la radio que transmite**.

Cambiar a ESP32 para obtener el WiFi "gratis" cuesta **5,8 días de autonomía**. Un co-procesador ESP32-C3 cuesta **USD 2,60** y no cuesta ni un día. Es una de las relaciones costo/beneficio más claras de todo el proyecto.

Y hay un segundo motivo, independiente de la batería: sobre el nRF52840 ya hay un firmware **validado**, con 99,28% de sensibilidad y 99,73% de predictividad positiva medidos contra MIT-BIH, más nueve suites de tests. Ese trabajo no se tira por USD 2,60.

**3. Para pacientes de 40-70 años, WiFi es la mejor experiencia sostenible.**

Vale ser honesto: **la SIM tiene la mejor experiencia de uso de las tres**. Cero configuración, funciona en todos lados. Si la batería y el costo no importaran, sería la respuesta.

Pero la comparación real es contra BLE, y ahí no hay competencia:

| | Qué le pedimos al paciente |
|---|---|
| BLE + app | Instalar una app, tener el celular cerca **todos los días**, no cerrarla, mantener el teléfono cargado. Un error cualquier día = datos perdidos ese día |
| WiFi | Elegir su red y escribir la contraseña **una vez**, en la clínica, con el técnico al lado |
| SIM | Nada |

BLE convierte al paciente en operador del sistema durante todo el estudio. WiFi le pide una acción única que además puede hacer otra persona por él. Para el usuario objetivo, esa diferencia es enorme.

**4. BLE no se descarta: se reubica.**

El firmware ya tiene BLE funcionando, con emparejamiento seguro. Sería un desperdicio apagarlo. Pero su lugar no es el transporte de datos:

- **Configuración inicial**: emparejar por BLE desde una app es más simple para el paciente que conectarse a una red `Holter-XXXX` y esperar que se abra el portal cautivo — un flujo que además funciona distinto en iOS y en Android.
- **Verificación de colocación del chaleco**: ver la señal en vivo mientras se acomodan los electrodos. Es exactamente el canal LIVE que el firmware ya expone.
- **App de acompañamiento del paciente**, que el [Módulo 5 de Requerimientos.md](../Requerimientos.md) ya pide: diario de síntomas, guía de colocación y lavado, estado de la batería.

La diferencia clave con la opción A: si el paciente no usa la app, **no se pierde ni un dato**. El chaleco sigue subiendo por WiFi. La app suma, no sostiene.

**5. Dejar previsto el lugar de la SIM es lo que evita rehacer la arquitectura después.**

Ésta era una condición explícita del proyecto: que la decisión sirva para el producto real. La opción C tiene una ventaja que las otras dos no pueden igualar — transmitir desde cualquier lado — y es perfectamente posible que el producto la necesite más adelante, para un paciente que trabaja fuera de la casa o que no tiene internet.

**Dejar el footprint del SIM7080G y el portasim dibujados en la PCB, sin poblar, cuesta USD 0 de BOM y unos pocos cm² de placa.** Con eso, la variante celular deja de ser un rediseño y pasa a ser una versión distinta del mismo producto, con el mismo firmware base. Es la diferencia entre "vía de evolución" en un documento y una vía de evolución de verdad.

### Lo que hay que resolver además, y no depende de esta decisión

**El buffer local de 16 MB es el riesgo más grande del sistema, y es independiente del canal que se elija.** Nueve horas y 56 minutos de memoria significan que un paciente que sale a trabajar un día completo pierde señal, con WiFi o con BLE. Los documentos actuales describen una microSD de 4-8 GB con "meses de margen", pero esa microSD todavía no existe en el hardware.

**Es el requerimiento número uno hacia el equipo de Biomédica.** Con una microSD de 8 GB, la ventana pasa de 10 horas a unos 4 meses y el problema desaparece por completo. Sin ella, la opción C (SIM) gana relevancia por descarte, porque es la única que no necesita que el paciente vuelva a un lugar determinado — y sería una lástima tomar la decisión de canal por una limitación de almacenamiento que se resuelve con una microSD de pocos dólares.

### Resumen en tres líneas

1. **WiFi por co-procesador**: conserva el firmware validado y el 96% de la autonomía, por USD 2,60.
2. **BLE para servicio y app**, nunca como camino de datos crítico.
3. **Footprint de SIM sin poblar**, para que la versión ambulatoria no sea un rediseño.

---

## 7. Anexo — supuestos, fuentes y qué falta medir

### 7.1 De dónde sale cada número

| Constante | Valor | Origen | ¿Hay que validarlo? |
|---|---|---|---|
| Frecuencia de muestreo | 500 Hz | `config.h: SAMPLE_RATE_HZ` | No — es código |
| Ratio de compresión | 12,80× (ruido real) | `DATAFLOW.md` §9.1, medido con `pio test -e native -f test_frame_codec` contra MIT-BIH + NSTDB | No — es medición |
| Caudal ECG 1 derivación | 468,6 B/s | Ídem | No |
| Caudal de impedancia | 468,6 B/s (cota superior) | **Estimación propia** — se le asigna el mismo caudal que al ECG | **Sí** — es esperable que comprima mejor |
| Capacidad de la flash | 16 MB → 9,94 h | `config.h` + `DATAFLOW.md` §9.3 | No |
| Batería | 1800 mAh, 3,7 V | `Datasheets/Bateria Descripcion.jpeg` (RS PRO) | No |
| Derating de batería | 85% | **Estimación propia**, práctica habitual | Razonable |
| Consumo del ADS1292R | 335 µA/canal | Datasheet TI SBAS502C | No |
| Consumo del nRF52840 grabando | 3,5 - 6 mA | **Estimación propia** a partir de la carga de DSP y del datasheet | **Sí — es la medición más importante que falta** |
| Consumo de un ESP32-C6 grabando | 8 - 15 mA | Estimación de familia | Sí, si alguna vez se considera B1 |
| Corriente BLE en transferencia | 7 mA | Estimación a partir del datasheet del nRF52840 | Sí |
| Throughput BLE efectivo | 20 KB/s | Conservador; el firmware ya limita a 12,5 - 62,5 KB/s | No |
| Corriente WiFi en TX | 120 mA | Valor típico de familia ESP32 | Sí |
| Throughput HTTPS en ESP32 | 500 KB/s | Rango observado 300 KB/s - 1 MB/s | Sí |
| Enganche WiFi (asociación+DHCP+TLS) | 4 s | Estimación | Sí |
| Corriente LTE-M en TX | 80 mA media, 250 mA pico | Datasheet SIM7080G | Sí |
| Throughput LTE-M | 25 / 100 / 380 kbps | Búsqueda web; spec del módulo 1.119 kbps | Sí — es el número más sensible de la opción C |
| Overhead de protocolo | +10% HTTP/TLS, +4% BLE | Estimación propia | Razonable |
| Precios USD a volumen | LCSC, ago. 2026 | Consulta web | Revalidar al comprar |
| Precios ARS | ago. 2026 | Consulta web; los de la opción C son **estimados** | **Sí — verificar en Mercado Libre al comprar** |
| Planes de datos Movistar | $1.490/1 GB, $6.100/5 GB | Consulta web, ago. 2026 | Revalidar |

### 7.2 Las tres mediciones que más cambiarían estas conclusiones

1. **Consumo real del nRF52840 grabando.** Es el denominador de todo. Si en vez de 6 mA fueran 10, las tres opciones bajan proporcionalmente y la brecha entre WiFi y SIM se achica. Se mide con un multímetro en serie sobre la batería, con el firmware clínico corriendo.
2. **Throughput real de LTE-M en el domicilio de un paciente tipo.** Es lo que decide si la opción C da 9 días o 3. Se mide con un breakout del SIM7080G y una SIM local, subiendo un archivo de 2 MB desde distintos puntos de una casa.
3. **Cuánto comprime realmente la señal de bioimpedancia.** Hoy se usa el caudal del ECG como cota superior. Si comprime el doble, todos los números del estudio de impedancia mejoran.

### 7.3 Inconsistencias detectadas en la documentación del proyecto

Este análisis se hizo contra el hardware real, y en el camino aparecieron cinco diferencias con lo que dicen hoy los documentos. Están corregidas en el resto de `info del proyecto/` y en `AGENTS.md`, y se listan acá para que quede registro de qué cambió y por qué:

| # | Lo que decían los documentos | Lo que hay en realidad |
|---|---|---|
| 1 | MCU de la familia ESP32, "el WiFi ya viene en el chip" | **XIAO nRF52840** — tiene BLE, **no tiene WiFi** |
| 2 | microSD de 4-8 GB → "meses de buffer" | **Flash SPI de 16 MB → 9,94 horas.** La microSD todavía no existe |
| 3 | 250 Hz × 3 canales × 16 bits, delta encoding ~50% → 65 MB/día | **500 Hz**, codec Rice sin pérdida medido en 12,80× → **40,5 MB/día** |
| 4 | LTE-M envía el batch horario en ~30 s | Exigiría ~720 kbps. LTE-M real: 25-380 kbps → **2,5 a 60 minutos** |
| 5 | Batería Li-Po de 500-1000 mAh | El datasheet de Biomédica es de **1800 mAh** |

Además, `AGENTS.md` registraba la arquitectura **SIM** como elegida mientras `info del proyecto/` registraba **WiFi**: se contradecían entre sí.

### 7.4 Fuentes externas

- Precios de módulos: [LCSC — SIM7080G](https://www.lcsc.com/product-detail/C2943992.html), [LCSC — ESP32-C3-MINI-1](https://www.lcsc.com/product-detail/C2838502.html), [LCSC — ESP32-C6-MINI-1](https://www.lcsc.com/product-detail/wifi-modules_espressif-systems-esp32-c6-mini-1-n4_C5736265.html)
- Especificación del módulo celular: [SIMCom SIM7080G](https://www.simcom.com/product/SIM7080G.html)
- Velocidades reales de IoT celular: [The True Speed of Cellular IoT — Monogoto](https://monogoto.io/2022/12/22/the-true-speed-of-cellular-iot/), [NB-IoT vs Cat-M1 vs Cat-1 — Hologram](https://www.hologram.io/blog/nb-iot-vs-cat-m1-vs-cat-1/)
- Planes de datos en Argentina: [Movistar prepago — packs y precios 2026](https://selectra.com.ar/empresas/movistar/prepago)
- Precios locales de referencia: [Innomake Bright (distribuidor Seeed en Argentina)](https://www.innomakebright.com.ar/), [Starware](https://tienda.starware.com.ar/)
- Datos del hardware y del firmware: repositorio `Holter-ECG-System` del equipo de Ingeniería Biomédica (`README.md`, `DATAFLOW.md` §3 y §9, `include/config.h`, `Datasheets/`)
