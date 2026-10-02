# Respuesta a la revisión de `INTEGRACION.md` (septiembre de 2026)

Contra su commit `e93290f`. Leímos el documento entero y cruzamos cada punto
contra nuestro código. Va por el mismo orden de prioridad que nos pasaron.

**Lo que hay que leer sí o sí, si no se lee nada más:** el punto 2. **No
reviertan los tres timeouts de §12.4.** La latencia no la bajamos en esta pasada.

---

## 1. §4.6 — el overflow que trababa la ingesta: **arreglado**

Tenían razón en todo, incluido el arreglo. Es la línea que señalaron, tal cual:

```python
# antes
expected = fresh[0].info.seq if (rebooted and fresh) else cursor + 1
# ahora
expected = fresh[0].info.seq if fresh else cursor + 1
```

Verificamos lo que ustedes decían que seguía valiendo, y sigue valiendo: un hueco
en **medio** del lote lo sigue cortando el bucle, sin cambios. Lo que cambió es
que un salto que el equipo no puede llenar deja de esperarse para siempre.

**Y el hueco ahora deja rastro**, que es lo que pedía §9.1 ("registro de cada
overflow con su hora de pared: el médico tiene que verlo"). Antes no dejaba
ninguno: se cortaba el ACK y listo.

- `ecg_batch` tiene una columna nueva, `preceding_seq_gap_frames`: cuántas tramas
  faltan entre nuestro cursor y la primera del lote.
- El procesamiento emite un evento `backlog_overflow` de severidad alta, anclado
  al punto exacto de la discontinuidad, **con la duración real de pared del
  hueco**. Sale de comparar el tramo anterior contra éste, no de estimarla.
- Genera además una alerta para el médico: es pérdida definitiva de registro.
- **Cruzamos el bit 0 de `X-Device-Status-Flags`** del mismo POST, como sugerían.
  Si viene, el evento queda marcado `cause: "device_confirmed"`; si no,
  `"inferred"`. El hueco es real en los dos casos; lo que cambia es si la causa
  la confirma el equipo.

El evento ocupa **cero muestras** a propósito: nuestro buffer de estudio es
continuo por construcción, así que un hueco no mete muestras, abre un tramo nuevo
en la línea de tiempo. Con ancho cero queda como una marca en el punto de la
discontinuidad en vez de una banda que taparía señal real. La duración viaja en
`gapMs`.

---

## 2. §12.2, §12.3 y §12.6 — latencia: **no la bajamos ahora**

Decisión de producto, tomada con los números a la vista. Lo decimos sin vueltas
porque de esto depende algo que ustedes tienen pendiente.

### ⚠️ NO reviertan los tres timeouts de §12.4

`HOLTER_LINK_BRIDGE_MAX_SILENCE_MS`, `HOLTER_LINK_HEALTH_TIMEOUT_MS` y
`BACKLOG_ACK_TIMEOUT_MS` **siguen haciendo falta como están**. Su parche deja de
ser un parche y pasa a ser la configuración vigente hasta nuevo aviso. Si los
revierten, cada lote va a hacer timeout.

### Lo que sí cambió, y que puede mover sus números

El trabajo cuadrático que causaba los `500` de §12.3 **está sacado** desde antes
de su medición de septiembre. La pirámide del visor se escribía reescribiendo un
objeto que crecía con el estudio: el lote 1 leía un objeto de S3, el lote 30 leía
treinta, y todo eso corría con la fila del estudio bloqueada, así que el POST
siguiente moría esperando ese lock a los 15 s del `statement_timeout`. Ahora se
escribe por chunks y hay un test que verifica que el trabajo por lote no crece
con el estudio.

Si repiten la corrida de §12.3, **esperamos bastante menos `500`**. Nos sirve
saber el número nuevo.

### Los 25,7 s: confirmado, es lo que sospechaban

Es el límite de duración de la función serverless. Y les debemos una mala
noticia: **no podemos devolverles un 504 explícito**. Cuando ese límite se
alcanza la invocación se termina y no queda nadie del lado del servidor para
contestar nada. El silencio a los 25,7 s es lo que hay.

### El diagnóstico completo, para que no lo investiguen dos veces

El piso fijo de ~5 s, independiente del tamaño del lote —que es exactamente lo
que ustedes midieron y les llamó la atención— se explica por tres cosas que se
suman:

1. **Casi todo POST cae en un arranque en frío.** El equipo postea una vez cada
   10 minutos y la plataforma no mantiene la instancia viva tanto tiempo, así que
   prácticamente cada lote arranca el proceso de cero. Esto explica por qué el
   costo es fijo y no por trama, y por qué su servidor de control local daba 93 ms
   con el mismo payload, la misma radio y en el mismo instante.
2. **Conexión nueva a la base en cada request** (TCP + TLS + handshake).
3. **El procesamiento pesado corre dentro de la misma invocación** que el ACK,
   después de responder pero antes de que la función termine.

Queda documentado en nuestro repo con el orden de impacto por si se retoma.

---

## 3. §13 — el visor: **hecho, y además imprimible**

Tenían razón en las cuatro observaciones. `paperSpeed` y `amplitude` llegaban y
se descartaban en la línea siguiente, el eje Y era autoescalado, el de tiempo
mostraba 10 s repartidos en el ancho que hubiera, y la grilla era la genérica de
uPlot.

Lo que hay ahora, siguiendo la receta de su `plotterSimulation.py`:

1. **La proporción, garantizada**: `px/s = 2,5 × px/mV`, o sea el cuadro de
   40 ms × 0,1 mV cuadrado. Los segundos visibles salen del ancho
   (`ancho_px / (barrido × px_por_mm)`), así que **agrandar la ventana muestra
   más segundos, no los mismos estirados**.
2. **Grilla de papel** de 1 mm y 5 mm a la escala real, en vez de las divisiones
   automáticas de uPlot.
3. **Pasos fijos**: 5/10/20 mm/mV y 25/50 mm/s.
4. **La escala rotulada en pantalla.** Y el zoom libre sigue existiendo para
   navegar, pero **en cuanto el viewport deja de corresponder al barrido
   declarado el rótulo cambia a "escala libre"** con un botón para volver. Sin
   eso alguien podría medir un QT sobre una escala que no es la que el cartel
   afirma, que nos pareció peor que no tener cartel.
5. **Milímetros físicos: hay informe imprimible.** Es donde sí salen exactos,
   como dice su punto 5. Tiras de 10 s dimensionadas en `mm`, con pulso de
   calibración de 1 mV al inicio de cada una. Verificado midiendo el SVG
   renderizado: el pulso mide 10,00 mm a 10 mm/mV y 20,00 mm a 20 mm/mV, y un
   latido de 0,8 s mide 19,9 mm a 25 mm/s y 39,8 mm a 50 mm/s.

**Dos cosas que nos encontramos verificando, y que les pueden servir** porque son
del mismo tipo que el defecto de su plotter de bring-up:

- El rango vertical se calculaba con el alto del área de trazado **antes de que
  el navegador la maquetara**, o sea con 0 px, y se quedaba clavado en un
  fallback. Efectivo medido: **22,9 mm/mV con la escala declarada en 10**, y sin
  cambiar al pasar a 20.
- El encuadre horizontal se hacía antes de que el motor de gráficos reservara el
  ancho de los ejes, así que quedaba corto de forma sistemática. Efectivo:
  **7,2 mm/s en el peor caso y 24,0 en el mejor, con 25 declarados.**

Los dos se veían perfectamente razonables en pantalla. Solo aparecieron midiendo
la geometría renderizada contra la declarada, que es la misma comprobación que
hizo caer su retícula rotulada a 25 mm/s corriendo a ~139. Ahora da 25,000 y
10,000 exactos, y hay tests de regresión.

**Una limitación que queda abierta y preferimos decirla.** Para un estudio largo,
lo que se dibuja en pantalla es una envolvente min/max decimada, no la señal
muestra a muestra: el visor elige el nivel de pirámide que entre en 20.000
puntos. La escala ya es correcta, pero a barrido diagnóstico conviene traer el
tramo crudo de la ventana visible. El informe imprimible sí usa las muestras.

---

## 4. §9.1 — dimensionar contra 5,1 h: **tomado**

Teníamos un solo umbral, de 10 h, sin nada que lo justificara. Coincidía casi
exactamente con las 9,94 h de la flash, o sea que avisábamos **después** de que el
log circular ya había empezado a pisar señal sin subir.

Ahora hay dos, dimensionados contra las 5,1 h:

- **1 h → aviso.** Seis ventanas de envío perdidas.
- **4 h → crítico.** Queda ~1 h antes del corte.

Corregimos además los números en nuestra documentación de arquitectura, que decía
9,94 h, "envío batch cada 1 h" (son 10 min) y pedía una microSD como requerimiento
abierto hacia ustedes. Eso último lo sacamos: su §11.5 punto 5 deja claro que esta
arquitectura no la contempla.

**Lo que todavía no hacemos:** no hay job periódico. El estado se calcula cuando
alguien abre el dashboard, así que un equipo callado "aparece" recién ahí. Lo
sabemos y está anotado.

---

## 5. §11.5 y §11.6 — los dos que había que charlar

### §11.5 — el evento neutro: **agregado**

`VestStatusEvent.alive`, en el `/ingest/device-status` que ya existe. Misma
autenticación, mismo cuerpo, sin endpoint nuevo.

Hace exactamente lo que pidieron y nada más: actualiza `last_seen_at`,
`last_battery_pct` y `firmware_version`. **No toca `placement_ok` ni
`placement_reported_at`, no crea alerta y no manda push.** Su argumento sobre
nuestro commit `9933292` era correcto y es el que decidió el diseño: un latido con
`signal_recovered` le estaría afirmando al paciente que el chaleco está bien
puesto cada diez minutos sin que el firmware tenga con qué sostenerlo.

Aprovechamos para empezar a guardar el `sqi` del cuerpo, que veníamos validando y
descartando.

**Cuando lo desplegemos les avisamos** y lo pueden empezar a llamar.

### §11.6 — el rewind de `seq`: **resuelto de nuestro lado**

Su análisis de por qué el `409` no alcanzaba era correcto: no se perdía señal,
pero el equipo reintentaba el mismo lote indefinidamente y el estudio no volvía a
avanzar solo, hasta que el backlog daba la vuelta.

Ahora, cuando detectamos un rewind confirmado (lote entero por debajo del cursor,
`bootId` distinto, y el rango **no** archivado), el backend **cierra el estudio en
curso y abre uno nuevo**, y la señal entra. Queda una alerta para que el médico
sepa por qué aparecieron dos estudios.

Para ustedes esto significa que **el `409 STUDY_SEQ_REWIND` ya no se emite** por
esta causa. Si su bridge lo estaba tratando como "no confirmar nada, reintentar",
no hay nada que cambiar: simplemente deja de aparecer.

La regla operativa de actualizar el firmware con el estudio cerrado **sigue siendo
buena práctica** —el backlog pendiente del equipo se pierde igual, y eso no lo
podemos recuperar desde acá— pero deja de ser lo único que separa al paciente de
un estudio trunco.

---

## 6. §3.2 — tramas anteriores al 16/9/2026: **no las marcamos, por ahora**

Los estudios que tenemos previos a esa fecha son de banco, no clínicos, así que no
justificaba un cambio de esquema. Queda anotado con el criterio escrito
(`started_at < 2026-09-16`) para aplicarlo si aparece uno real.

Gracias por el aviso igual: es el tipo de cosa que en seis meses nadie se acuerda.

---

## Dos cosas nuestras

### §11.1 les falta una fila en la tabla de cabeceras

`X-Bridge-Epoch-Ms`, `X-Time-Sync-Source` y `X-Time-Sync-Uncertainty-Ms` son
**obligatorias** (`ingest_require_time_sync` está en `true`), no opcionales, y no
figuran en esa tabla. Su `BridgeTimeSync.h` ya las manda y está validado contra
el backend real, así que **no hay nada que cambiar en el firmware**: es solo el
documento, que las describe en §11.4 sin ponerlas en la lista.

### Sí, mándenlos: leemos las cuatro cabeceras de diagnóstico

Queda contestada la pregunta que dejaron abierta en §11.5 —*"en cuanto nos digan
que los van a leer"*—. Las cuatro se archivan por lote. Qué hacemos con cada una:

| Cabecera | Qué hacemos |
|---|---|
| `X-Device-Status-Flags` | Los bits 2, 4 y 6 generan alerta **crítica** de falla del equipo, con debounce de 1 h. El bit 0 confirma la causa de un hueco; el bit 3 genera su propio evento |
| `X-Device-Loss-Flags` | Se archiva. Es pérdida anterior a la flash |
| `X-Device-Lead-Flags` | Se archiva. **No dispara nada** |
| `X-Device-Backlog-Seconds` | Se archiva. **No alerta** |

**Les hicimos caso en las dos advertencias**, y conviene que sepan que las
tomamos en serio porque son las que más fácil se ignoran:

- **Los bits 0 y 1 de lead-off no los leemos en ninguna parte.** Su medición es
  concluyente: 0 % de detección en los dos electrodos cuya pérdida invalida la
  señal, y disparo espurio con el de tierra informando el electrodo equivocado.
  Hay un test que verifica que no generan nada, con el motivo escrito al lado,
  para que a nadie se le ocurra "aprovecharlos" más adelante.
- **`X-Device-Backlog-Seconds` no alimenta ningún umbral.** Con +98 % de error
  estaríamos diciendo 20 minutos cuando son 10, que es exactamente lo que nos
  advirtieron. Usamos la resta de `t0Ms` contra `X-Device-Uptime-Ms`.

Y sobre el bit 5: entendimos que en la cabecera significa "estuvo suelto en algún
momento desde el último POST confirmado" y **no** el veredicto vigente del paquete
de STATUS. No lo usamos para pedirle al paciente que se recoloque nada.

**SQI y RSSI: mándenlos también.** Son dos de los cinco campos que
`holter_health_out()` devuelve en `None` fijo y los podemos empezar a mostrar.

---

## Resumen para el pizarrón

| Punto | Estado |
|---|---|
| §4.6 overflow | ✅ Arreglado, con el hueco registrado y alertado |
| §12 latencia | ⛔ No en esta pasada. **No reviertan los timeouts de §12.4** |
| §12.3 los `500` | ✅ La causa (trabajo cuadrático) está sacada. Repitan la medición |
| §12.6 los 25,7 s | ✅ Confirmado: límite de la función. No podemos dar un 504 |
| §13 visor | ✅ Escala clínica + informe imprimible |
| §9.1 umbrales | ✅ Recalculados contra 5,1 h |
| §11.5 `alive` | ✅ Agregado. Les avisamos al desplegarlo |
| §11.6 rewind | ✅ Auto-recuperación. El `409` deja de aparecer |
| §3.2 estudios viejos | ⏸ Anotado, sin cambios |
| §11.1 cabeceras de diagnóstico | ✅ Las leemos. Manden también SQI y RSSI |
