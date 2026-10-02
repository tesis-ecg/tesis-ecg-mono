# ADR-005: Línea de tiempo de pared y niveles por chunks

## Estado

Aceptado. Reemplaza parcialmente a [ADR-004](004-versioned-ecg-manifest.md): el manifest pasa a v3.

## Contexto

Dos problemas con la misma raíz, medidos por el equipo de Biomédica el 8/9/2026 contra el
despliegue real.

**El eje del ECG no es hora real.** El buffer de muestras de un estudio se arma pegando cada lote
al anterior (`start_sample_index = study.samples_count`), así que es continuo por construcción. La
grabación no lo es: el chaleco se reinicia, se queda sin batería, se sale del alcance del WiFi. En
cuanto falta señal, el índice de muestra deja de corresponder a la hora y todo lo posterior queda
fechado antes de cuando se midió. Además el ancla se derivaba de nuestra hora de recepción, que
arrastra la latencia del pedido: 5,1 s de mediana y picos de 22,8 s.

**La ingesta devolvía `500` bajo carga sostenida.** `rebuild_pyramid` releía de S3 todas las
envolventes del estudio en cada lote y reescribía los seis niveles. Cuadrático sobre la duración
del estudio, corriendo con `FOR UPDATE` sobre la fila del estudio. El POST siguiente esperaba ese
lock hasta morir en el `statement_timeout` de 15 s — el tiempo exacto que midieron.

## Decisión

**El tiempo se ancla por tramo de arranque.** El puente ESP32-C3 manda su epoch UTC leído en el
mismo instante que el `millis()` del equipo (`X-Bridge-Epoch-Ms` y compañía, contrato en
[integracion-ingesta-con-horario.md](../integracion-ingesta-con-horario.md)). El backend deriva
`boot_epoch = bridge_epoch − uptime`, el instante en que el `millis()` valía cero, y de ahí
`UTC(trama) = boot_epoch + t0Ms`. Las dos cifras de la resta las mide el mismo lado del enlace,
así que la red queda afuera.

**`study_timeline_segment` materializa las corridas contiguas.** Se abre un tramo cuando cambia el
`bootId`, cuando `t0Ms` retrocede con el mismo `bootId` (wraparound a los 49,7 días) o cuando el
equipo dejó de grabar más que la tolerancia. Cada tramo lleva su ancla y su corrección de deriva,
ajustada por mínimos cuadrados sobre las anclas del mismo arranque con la pendiente acotada a
±200 ppm.

Ese tercer corte se mide en el **reloj del equipo** y no en hora de pared, aunque el hueco que
describe sea de hora de pared. Con el mismo `bootId` y `t0Ms` monótono los dos lotes vienen del
mismo `millis()`, así que la distancia entre ellos es exacta. Compararlos por su hora de pared
comparaba dos anclas distintas, y con el ancla vieja —la que corre mientras
`ingest_require_time_sync` está apagado— cada ancla lleva adentro la latencia de su propio pedido:
un pico de los que midió Biomédica partía en dos una grabación continua, y el tramo siguiente
arrancaba antes de que terminara el anterior.

**Los niveles de la pirámide se escriben por chunks.** Cada lote anexa lo suyo a cada nivel, con un
carry por nivel que mantiene los buckets alineados a la grilla del estudio. Los chunks se compactan
al cruzar un umbral y al cerrar el estudio.

## Alternativas descartadas

- **Estampar hora absoluta en cada trama.** 6 B × ~350.000 tramas/día ≈ 2 MB/día, y exigiría que el
  nRF52840 conozca UTC, que no la conoce.
- **Agregar un RTC a la placa.** Es lo más robusto y no está en alcance: cambia el hardware. La
  arquitectura lo admite después como una fuente de ancla más.
- **Seguir anclando con la hora de recepción.** Cero cambios para Biomédica, pero el error de red
  queda dentro de la hora del paciente y no hay forma de acotarlo.

## Consecuencias

- El eje del visor es hora de pared real y los huecos se dibujan como huecos.
- El trabajo de procesamiento por lote pasa de O(estudio) a O(lote).
- El manifest sube a v3: `levels[].chunks`, `timeline`, y `startEpochMs`/`endEpochMs` en cada
  anotación. El cliente concatena chunks igual que ya hacía con `segments`.
- Las cabeceras de hora son obligatorias por contrato, detrás de
  `ingest_require_time_sync`, que arranca apagado hasta que Biomédica despliegue su firmware.
- Los estudios ya ingeridos se reconstruyen con `python -m app.scripts.backfill_timeline`, con la
  precisión vieja declarada como `server_receive`.
- Un `seq` que rebobina y **no** está archivado deja de contarse como duplicado: devuelve
  `409 STUDY_SEQ_REWIND` en vez de confirmarle al equipo señal que nunca llegó
  (`INTEGRACION.md` §11.6).
