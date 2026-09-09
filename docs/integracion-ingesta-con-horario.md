# Hora de pared en el ECG — qué necesitamos del puente WiFi

**Para:** equipo de Ingeniería Biomédica
**Afecta:** el firmware del co-procesador ESP32-C3. **No afecta al nRF52840.**

---

## 0. El resumen, para que no haga falta leer el resto

El visor del médico tiene que mostrar **la hora real en que el chaleco tomó cada
medición**, no el tiempo transcurrido desde que arrancó el estudio. Hoy no
podemos: el equipo no tiene RTC y nosotros veníamos derivando la hora de nuestra
propia hora de recepción, que arrastra la latencia de la red.

Lo que necesitamos de ustedes son **tres cabeceras HTTP nuevas** en el POST que
ya hacen. Nada más.

| Cabecera                     | Tipo            | Qué es                                           |
| ---------------------------- | --------------- | ------------------------------------------------ |
| `X-Bridge-Epoch-Ms`          | uint64          | Epoch UTC en milisegundos, leído por el ESP32-C3 |
| `X-Time-Sync-Source`         | `ntp` \| `none` | De dónde salió esa hora                          |
| `X-Time-Sync-Uncertainty-Ms` | uint32          | Cuánto creen que puede estar errada              |

**No cambia un solo byte de la trama de 256 bytes.** No cambia el `seq`, ni el
`bootId`, ni el `t0Ms`, ni la semántica del ACK, ni el formato de la flash.
Cuesta ~60 bytes por POST, y ustedes hacen un POST por ciclo de envío.

Lo único delicado es **cuándo** se lee ese epoch, y va en la sección 3.

---

## 1. Por qué hace falta

`INTEGRACION.md` §5 ya lo dice: el equipo no tiene reloj de tiempo real, todos
los `t0Ms` son milisegundos desde el arranque, y convertirlos a UTC es
responsabilidad nuestra. Veníamos haciéndolo así:

```
epoch_anchor_ms = nuestra_hora_de_recepción − X-Device-Uptime-Ms
```

Eso funciona, pero mete dentro de la hora del paciente todo lo que pasa entre que
el equipo lee su `millis()` y que nuestro servidor procesa el pedido: la latencia
del WiFi del domicilio, la de internet, y el arranque de nuestra función. Sus
propias mediciones del 8 de septiembre le ponen número a eso: **mediana de 5,1 s,
p95 de 6,3 s y picos de 22,8 s.** Cada uno de esos segundos se le sumaba de más
a la hora de la medición.

Con el epoch leído en el ESP32-C3, el cálculo pasa a no depender de la red:

```
boot_epoch_ms = X-Bridge-Epoch-Ms − X-Device-Uptime-Ms
UTC(trama)    = boot_epoch_ms + t0Ms
```

`boot_epoch_ms` es **el instante UTC en que el `millis()` del equipo valía cero**
para ese arranque. Las dos cifras que lo componen las mide el mismo lado del
enlace, así que nada de lo que pase después lo puede corromper.

### Por qué esto y no otra cosa

- **No estampamos hora absoluta por trama.** Serían 6 bytes × ~350.000
  tramas/día ≈ 2 MB/día extra, y obligaría a que el nRF52840 conozca UTC, que no
  la conoce. Una ancla por POST cuesta ~60 bytes en total.
- **No pedimos un RTC.** Es un cambio de hardware y de PCB que hoy no está en
  alcance. Si algún día aparece, esta arquitectura no cambia: sería otra fuente
  de ancla y la marcaríamos con `X-Time-Sync-Source: rtc`.
- **No pedimos que el equipo se sincronice.** El nRF52840 sigue contando
  `millis()` y nada más. Toda la noción de UTC vive en el ESP32-C3, que ya está
  asociado a WiFi cuando postea.

---

## 2. Las tres cabeceras

Se agregan a las que ya manda el puente (`INTEGRACION.md` §11.1). Las existentes
no cambian.

### `X-Bridge-Epoch-Ms` — obligatoria

Milisegundos desde el epoch UNIX (1970-01-01 00:00:00 UTC), en decimal, sin
signo. Es la hora **UTC**, no la hora local de Argentina: no le resten ni le
sumen las tres horas. La conversión a hora local la hace el navegador del médico.

Ejemplo: `X-Bridge-Epoch-Ms: 1757383200123`

**Validación del lado nuestro.** Rechazamos con `422` un valor que caiga a más de
unas horas de nuestra propia hora. Un puente con SNTP roto que mande `0` no puede
archivar un estudio fechado en 1970.

### `X-Time-Sync-Source` — obligatoria

Uno de dos valores literales:

- `ntp` — la hora salió de una sincronización SNTP exitosa en este ciclo de
  envío, o de una anterior propagada con el reloj interno del ESP32-C3.
- `none` — SNTP falló y el valor de `X-Bridge-Epoch-Ms` es la mejor estimación
  que tienen (por ejemplo, la última hora conocida más el tiempo transcurrido).

**Nunca omitan la cabecera para señalar que falló.** Manden `none` y su mejor
estimación. Un tramo con `none` lo marcamos como precisión degradada y el médico
lo ve señalado en el visor; un tramo sin cabecera lo rechazamos.

### `X-Time-Sync-Uncertainty-Ms` — obligatoria

Su propia estimación del error, en milisegundos, sin signo. Sugerencia de cómo
calcularla:

- Recién sincronizados por SNTP: la mitad del round-trip de la consulta, más lo
  que hayan medido de dispersión. Típicamente decenas de milisegundos.
- Propagando una sincronización vieja: lo anterior más la deriva acumulada del
  cristal del ESP32-C3 desde entonces. Con 20 ppm son ~72 ms por hora.
- Con `source: none`: lo que les parezca honesto. Si no tienen idea, un número
  grande (por ejemplo 3.600.000, o sea una hora) es mucho mejor que un cero
  optimista.

No la usamos para rechazar nada. La usamos para pesar las anclas cuando ajustamos
la deriva, y para decirle al médico cuánto vale la hora que está mirando.

---

## 3. La regla que importa: leer las dos cifras en el mismo instante

`boot_epoch_ms = X-Bridge-Epoch-Ms − X-Device-Uptime-Ms`

Esa resta solo tiene sentido si **las dos cifras describen el mismo instante**.
Si leen el epoch al despertar y el `millis()` treinta segundos después, la hora
de todas las tramas de ese tramo queda corrida treinta segundos.

`INTEGRACION.md` §11.4 ya pedía que `X-Device-Uptime-Ms` fuera el `millis()` del
equipo **en el instante del POST** y no el del último STATUS, extrapolándolo con
el reloj del puente. Esa regla sigue valiendo igual. Lo que se agrega es que
`X-Bridge-Epoch-Ms` tiene que ser el epoch del puente **en ese mismo instante**.

En la práctica: calculen las dos, juntas, justo antes de armar el pedido.

```c
// Las dos lecturas, pegadas. Cualquier trabajo entre medio corre la hora.
uint32_t now          = millis();                       // reloj del puente
uint64_t bridge_epoch = epochBaseMs_ + (now - epochBaseTick_);
uint32_t device_uptime = deviceUptimeAt_ + (now - deviceUptimeTick_);
```

Un desfasaje de un segundo entre las dos lecturas corre **una hora entera de
estudio** un segundo. No es catastrófico, pero es exactamente el error que este
cambio viene a eliminar, así que vale hacerlo bien.

---

## 4. Cuándo sincronizar

**Una vez por ciclo de despertar, antes del primer POST de ese ciclo.** No hace
falta más.

- El puente ya se asocia a WiFi para postear; SNTP es un intercambio UDP de unos
  pocos cientos de bytes y no cambia el presupuesto de batería de forma
  apreciable.
- Si SNTP falla, **no aborten el envío**. Posteen igual con
  `X-Time-Sync-Source: none` y su mejor estimación. Perder señal por no saber la
  hora sería mucho peor que archivarla con la hora aproximada.
- Si el ciclo manda varios POST, alcanza con una sincronización para todos; lo
  que cambia entre POST es el `millis()`, y eso ya se recalcula.

**Guarden la última sincronización en memoria RTC**, igual que ya hacen con los
acumuladores de flags de diagnóstico. Así un ciclo que no logra hablar con el
servidor NTP puede propagar la hora del ciclo anterior en vez de caer a `none`.

---

## 5. Qué hacemos nosotros con esto (para que sepan qué esperar)

No hace falta que implementen nada de esta sección. Está para que puedan
verificar que el resultado es el que esperaban.

### Tramos de arranque

Agrupamos las tramas en **tramos**: corridas máximas que comparten `bootId` con
`seq` y `t0Ms` monótonos. Abrimos un tramo nuevo cuando pasa cualquiera de estas
tres cosas:

1. cambia el `bootId` — reinicio, watchdog, cambio de batería;
2. `t0Ms` retrocede con el mismo `bootId` — el wraparound de `millis()` a los
   49,7 días que ya está documentado en §5;
3. el `t0Ms` salta hacia adelante más que una tolerancia — el chaleco estuvo
   despierto y no grabó.

Ojo con el tercero: se mide sobre **su** `t0Ms`, no sobre nuestra hora. Un chaleco
fuera del alcance del WiFi sigue grabando en su flash, así que no deja hueco y le
corresponde un solo tramo. Uno apagado tampoco cae acá: vuelve con otro `bootId` y
lo toma la primera regla.

Cada tramo lleva su propia ancla. Eso es lo que hace que un chaleco que se queda
sin batería en medio de una medición no arruine la hora de todo lo que viene
después: el corte queda como un hueco real, con su duración real, y lo que
grabó después del corte tiene su hora correcta.

### Corrección de deriva

Un cristal de 20 ppm corre ~1,7 s por día, así que con una sola ancla un estudio
de 24 h no cerraría en nuestro objetivo de ±1 s. Como llega un ancla por ciclo de
envío, ajustamos una recta por tramo sobre todas las anclas recibidas, con la
pendiente acotada a 1 ± 200 ppm para que una sincronización mala no deforme el
estudio entero.

Consecuencia práctica para ustedes: **cuantos más ciclos de envío, mejor la
hora.** Un chaleco que postea cada 10 minutos queda mejor anclado que uno que
postea una vez por día. No hace falta que cambien nada por esto.

### Qué ve el médico

El eje del gráfico pasa a ser hora de pared real y continua. Si el chaleco estuvo
cuatro horas sin grabar, el eje muestra esas cuatro horas vacías en vez de pegar
los dos tramos como si fueran continuos. Que falte registro es información
clínica y hasta ahora se estaba perdiendo.

---

## 6. Ejemplo completo

```bash
curl -i -X POST "https://tesis-ecg-api.vercel.app/ingest/ecg-frames" \
  -H "Authorization: Bearer <api-key>" \
  -H "X-Device-Serial: HOLTER-AR-000" \
  -H "X-Device-Uptime-Ms: 89000" \
  -H "X-Bridge-Epoch-Ms: 1757383200123" \
  -H "X-Time-Sync-Source: ntp" \
  -H "X-Time-Sync-Uncertainty-Ms: 45" \
  -H "X-Firmware-Version: 1.4.2" \
  -H "X-Battery-Pct: 78" \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@lote.bin"
```

Con esos valores, el arranque del equipo quedó en
`1757383200123 − 89000 = 1757383111123`, y una trama con `t0Ms = 60000` se
archiva a `1757383171123`.

### Errores nuevos

| Código                          | Cuándo                                                | Qué mirar                                                                     |
| ------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------- |
| `422 DEVICE_TIME_SYNC_REQUIRED` | falta alguna de las tres cabeceras                    | que el puente las esté armando en todos los caminos, incluido el de reintento |
| `422 DEVICE_TIME_INVALID`       | `X-Bridge-Epoch-Ms` cae fuera de la ventana razonable | SNTP devolvió basura, o el puente propagó una hora sin base válida            |

Los códigos que ya existen (`401`, `409`, `413`, y el `422` por
`X-Device-Uptime-Ms` faltante) no cambian.

---

## 7. Coordinación del despliegue

Las cabeceras son **obligatorias** en el contrato. Para no dejarlos sin poder
postear el día que desplegamos, del lado nuestro esto sale detrás de un
interruptor que arranca apagado:

1. Desplegamos con el interruptor apagado. El firmware actual sigue funcionando
   exactamente igual; si mandan las cabeceras, ya las usamos.
2. Ustedes sacan la versión del puente que las manda y nos avisan.
3. Prendemos el interruptor. A partir de ahí, un POST sin las cabeceras da `422`.

Mientras el interruptor está apagado, un POST sin cabeceras se archiva con el
ancla vieja y queda marcado como precisión degradada, así que van a poder ver la
diferencia entre las dos formas en el mismo visor.

**Cómo probarlo antes de tener hardware nuevo:** el simulador de chalecos del
portal (`/__sim/vest`, visible para administradores) manda las cabeceras nuevas y
permite forzar reinicios y cortes de señal. Sirve para ver cómo queda el gráfico
con huecos antes de reproducirlo con el equipo real.

---

## 8. Sobre los hallazgos de latencia de su informe

Están tomados y en curso, y son nuestros. El resumen corto:

- **Los ~4 s de costo fijo hasta el 202** son en buena parte el arranque en frío
  de la función serverless, más una conexión nueva a la base de datos por pedido
  con un round trip desperdiciado, más una escritura a S3 que bloqueaba el
  procesamiento. Los tres se atacan.
- **Los `500` bajo carga sostenida** ya tienen causa identificada, y sus números
  la confirman. Un trabajo posterior al ACK reconstruía las vistas alejadas del
  ECG leyendo **todo** el estudio acumulado en cada lote, mientras tenía tomada
  la fila del estudio en la base. El POST siguiente esperaba esa misma fila y
  moría a los 15 s por nuestro `statement_timeout`. **Los 15,0 s que midieron son
  exactamente ese timeout**, no un pico casual. Pasa a ser trabajo incremental y
  a no tomar la fila mientras habla con S3.
- **El p95 sostenido**, que es el número que nos pidieron para dimensionar
  `BACKLOG_ACK_TIMEOUT_MS`, se los pasamos medido cuando estén los dos arreglos.
  Adelanto una recomendación igual: **los 3 s actuales quedan cortos aunque
  cerremos por debajo de 1 s**, porque el arranque en frío no desaparece del todo
  mientras sigamos en serverless. Denle margen.

Gracias por el informe. La medición contra un servidor local como control es lo
que hizo que el diagnóstico fuera directo en vez de una discusión sobre de quién
era el problema.
