# Benchmark de los dos detectores de R (`nk` contra `pt`)

En `app/ml/rpeak_detection.py` conviven dos detectores de complejos QRS que todavía no
se unificaron. La decisión es unificarlos **con Se y PPV medidos**, y esto es lo que
los mide:

| | función | qué recibe | quién lo consume | cómo corre en producción |
|---|---|---|---|---|
| `nk` | `detect_rpeaks(clean_signal(raw))` | la señal de `nk.ecg_clean` | el motor: gate de calidad (bSQI), ritmo y morfología | por bloques de 300 s, con 60 s de contexto y 30 s de lookahead |
| `pt` | `detect_r_peaks(raw)` | los mV crudos | `/holter-metrics`: FC, pausas, VFC, ST (`app/ml/beats.py`) | una pasada por lote: la señal nueva con 30 s de contexto a cada lado |

Cada uno recibe **lo que recibe en producción**. El código está en
`tools/physionet/detectors.py` (MIT-BIH y NSTDB) y `tools/vest/detectors.py` (chaleco).

## Cómo se corre

```bash
cd back
uv run python -m tools.physionet.evaluate --detectors --download        # una vez: 48 de mitdb + nstdb 118/119
uv run python -m tools.physionet.evaluate --detectors --jobs 8          # todo: ~10 s con 8 procesos
uv run python -m tools.physionet.evaluate --detectors --summarize-only  # rehace el reporte desde el JSON
uv run python -m tools.physionet.evaluate --detectors --part mitdb --records 113 222
uv run python -m tools.physionet.evaluate --detectors --part vest       # solo el chaleco
uv run python -m tools.vest.detectors captura_canal2_gel_limpia         # ídem, secuencial
```

`python -m tools.physionet.detectors ...` es lo mismo. La corrida deja los conteos por
registro (TP, FN, FP, corridos, percentiles del desvío y el diagnóstico de errores) en
`back/tools/physionet/data/detectors_results.json`, que está en el `.gitignore` como
el resto de `data/`. Las capturas del chaleco se leen de
`../Holter-ECG-System/capturas/captura_canal2_*.txt` (`--captures-dir` para otra ruta).

Los números de abajo son de una corrida sobre `495128d` (`rpeak_detection.py`,
`quality.py` y `beats.py` sin cambios locales) con NeuroKit 0.2.13, SciPy 1.18.1 y
wfdb 4.3.1.

## Método

**Señal.** MIT-BIH y NSTDB, canal 0, llevado de 360 a 500 Hz con el mismo
`evaluate.load_record` del motor (`resample_poly(x, 500, 360)`); las anotaciones se
pasan con `sample * 500 // 360`, igual que ahí. El canal 0 es MLII en 45 de los 48
registros: en 102 y 104 es V5 y en 114 también (el registro tiene los canales
invertidos).

**Registro entero y producción.** `nk` y `pt` corren sobre el registro **entero**, de
una vez. Producción no los corre así, y para `pt` la diferencia pesa; por eso cada
banco mide también dos variantes que reproducen el corte de producción:

- `nk_por_bloque`: bloques de 300 s, cada uno limpiado y detectado con 60 s de
  contexto antes y 30 s de lookahead (`ml_analysis_*_seconds`).
- `pt_por_lote`: lo que hace `append_beat_analysis`, que corre en **cada lote**
  (`processing._process_one_batch`), no en pasadas largas: lee la señal nueva con
  `BEAT_CONTEXT_SECONDS` (30 s) a cada lado, corre `detect_r_peaks` sobre eso y se
  queda con los latidos de la parte nueva. `detect_r_peaks` estima el umbral con sus
  primeros 8 s, así que **reaprende en cada lote**. Un lote es un POST del puente,
  hasta 48 tramas de 256 B: ~26 s de señal a los 468,6 B/s medidos (menos con el
  chaleco flojo). El tope de 2 h por pasada (`BEAT_SAMPLES_PER_PASS`) solo cuenta al
  ponerse al día con señal sin analizar. Con lotes de 15 s el resultado es el mismo; con
  lotes más largos `pt_por_lote` se acerca a `pt` (ver más abajo).

Y una variante que **no está en producción**, para evaluar a `pt` como candidato:
`pt_reinicio`, `pt` sobre el registro entero que vuelve a arrancar (y a aprender el
umbral) tras 5 s sin latidos. En el chaleco se suma `pt_sin_marcas` (ver Chaleco).

**Referencia.** Las anotaciones de latido de MIT-BIH, con el conjunto de símbolos de
`bxb` / `wfdb`: `N L R B A a J S V r F e j n E / f Q ?`. Quedan afuera los que no son
latidos: ritmo (`+`), calidad (`~`), artefacto aislado (`|`), onda P no conducida
(`x`), ondas de flutter ventricular (`!`) y comentarios (`"`). Los tramos de flutter o
fibrilación ventricular entre `[` y `]` se excluyen de la estadística, como pide
ANSI/AAMI EC57: ahí no hay QRS que detectar. En MIT-BIH solo aparecen en 207 (143 s).
El conjunto de símbolos de `evaluate.py` (`NLRej` + `VASFaJE/fQ`) no trae `B S r n ?`;
en los 48 registros eso son 2 latidos (dos `S`).

**Criterios.** El de referencia es **EC57**: lo que hace `bxb` sin opciones, que es la
implementación de referencia de la norma. Arranca la comparación a los **5 min** del
inicio (`-f`; el período de aprendizaje que la norma le concede al detector), la
sigue hasta el final del registro (`-t`) y empareja con una ventana de **±150 ms**
(`-w 0.15`: "la máxima diferencia absoluta" entre los tiempos de las anotaciones;
[man page de `bxb`](https://physionet.org/physiotools/wag/bxb-1.htm)). Cambiar `-f` o
`-w` es, para `bxb`, una "non-standard comparison". Además se reportan tres criterios
**más estrictos que `bxb`**: desde el segundo 1 (solo se descartan el primer y el
último segundo, por el transitorio de los filtros de fase cero; el arranque del umbral
sí cuenta) y a ±150, ±75 y ±50 ms.

Con eso se separan dos tipos de error. **Detección**: FN y FP a ±150 ms. **Ubicación
del fiducial**: un latido detectado a ±150 ms pero a más de 50 ms de la anotación
(`corridos` en las tablas); el latido está, pero el R quedó sobre otra parte del
complejo. A ±75 y ±50 ms esos latidos cuentan como un FN más un FP.

**Emparejamiento.** 1 a 1 y **óptimo**: el máximo número de pares con
|detección − anotación| ≤ tolerancia y, entre los máximos, el de menor error absoluto
total. Se parte la unión ordenada de los dos trenes donde dos puntos consecutivos
quedan a más de la tolerancia (ningún par puede cruzar ese hueco) y cada tramo con más
de un candidato se resuelve con el algoritmo húngaro (`scipy.optimize.linear_sum_assignment`,
con un costo prohibitivo fuera de tolerancia). Contra el húngaro sobre la matriz
completa, en 3000 casos aleatorios, da el mismo número de pares y el mismo costo. No es
la regla de emparejamiento de `bxb.c` línea por línea: la región y la ventana son las
de `bxb`, el emparejamiento es el óptimo. A ±150 ms y 500 Hz la tolerancia es
|d| ≤ 75 muestras; a ±75 y ±50 ms, ≤ 37 y ≤ 25.

**Conteo.** Se empareja sobre los trenes enteros y después se cuenta dentro de la
región evaluada: TP y FN por la posición de la anotación, FP por la de la detección.
Se = TP / (TP + FN), PPV = TP / (TP + FP), DER = (FN + FP) / latidos. Los totales son
**brutos** (los "gross" de EC57): Σ TP, Σ FN y Σ FP sobre los registros.

**NSTDB.** Los registros 118 y 119 con ruido de movimiento de electrodo (`em`) a
24, 18, 12, 6, 0 y −6 dB. Cada uno se puntúa por separado en los bloques con ruido y
en los limpios (`evaluate.noisy_mask`: 5 min limpios y después bloques alternos de
2 min). La fila `limpio` es el registro original de `mitdb` puntuado sobre los mismos
bloques, así que entre filas lo único que cambia es el ruido. Los bloques con ruido
empiezan a los 5 min, dentro de la región de EC57; los limpios incluyen los 5 min
iniciales.

**Chaleco.** Las 16 capturas de canal 2, leídas con `load_capture` y `build_flags` de
`tools.vest.evaluate`. No hay anotación de un cardiólogo: la referencia es el detector
del firmware (`FLAG_R_PEAK`), y se mide solo en las ventanas GOOD del gate, el mismo
`assess_quality` con los mismos argumentos que corre `tools.vest.evaluate`. Dos
versiones del tren de referencia, con los mismos latidos:

- `nominal`: el de producción, `firmware_rpeaks` + `compensate_firmware_peaks` con el
  retardo fijo de `ml_firmware_peak_lag_ms` (250 ms) y el refractario de 300 ms.
- `por latido`: sin dobletes igual que el anterior, pero corrido por el retardo que el
  firmware informa en cada latido: `r_lag_ms` (en el dominio de la señal de
  diagnóstico) más el `fir_delay` del `#META` (80 muestras, el FIR de 161 taps). Es
  como alinea los latidos `tools/promediar_latidos.py` del repo hermano.

**¿El gate sesga la medición a favor de `nk`?** En principio podría: `nk` es el
`detected_peaks` del gate, y una ventana que pasa la Capa A, la línea plana y los
índices espectrales queda MARGINAL si el bSQI entre el firmware y `nk` no llega a
0,80 (`quality._assess_window`; `no_beats` exige que **los dos** estén vacíos). `pt` no
participa. Para medirlo, la región `GOOD sin bSQI` corre el mismo gate con el tren del
firmware como `detected_peaks`: el bSQI da 1 en toda ventana con latidos del firmware,
y la región queda en Capa A + línea plana + índices espectrales + "el firmware vio
algún latido", que no depende de ningún candidato por construcción. **En estas 16
capturas MARGINAL es 0 % y GOOD coincide muestra a muestra con `GOOD sin bSQI` en las
16**: el bSQI no excluyó ninguna ventana, así que no hubo sesgo a favor de `nk`. El
sesgo que sí queda es el de la referencia: **el detector del firmware, sin validar**,
no un cardiólogo. Si en otros datos MARGINAL deja de ser 0, la fila `GOOD sin bSQI`
sigue siendo la que no depende del detector.

Las capturas se agrupan en `con marcas` (alguna muestra con `LEAD_OFF` o
`ADC_SATURATED`, 8) y `sin marcas` (8). Es una aproximación: lo que ciega a `pt` no
es la marca (ver abajo).

`pt_sin_marcas` es `pt` sobre la captura con las muestras marcadas (y 0,5 s alrededor)
reemplazadas por una recta entre los bordes: lo que haría una guarda por flags antes de
`analyze_window`. Tampoco está en producción.

## Resultados

### MIT-BIH: totales brutos

| conjunto | criterio | latidos | nk Se | nk PPV | nk DER | pt Se | pt PPV | pt DER | nk corridos | pt corridos |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| todos (48) | **EC57**: desde 5 min, ±150 ms | 91 285 | 0,9888 | 0,9819 | 0,0295 | 0,9900 | 0,9973 | 0,0126 | 0,0196 | 0,0219 |
| todos (48) | desde 1 s, ±150 ms | 109 374 | 0,9892 | 0,9818 | 0,0291 | 0,9901 | 0,9943 | 0,0156 | 0,0201 | 0,0249 |
| todos (48) | desde 1 s, ±75 ms | 109 374 | 0,9837 | 0,9763 | 0,0401 | 0,9819 | 0,9861 | 0,0319 | | |
| todos (48) | desde 1 s, ±50 ms | 109 374 | 0,9692 | 0,9619 | 0,0693 | 0,9652 | 0,9693 | 0,0654 | | |
| sin marcapasos (44) | **EC57**: desde 5 min, ±150 ms | 83 978 | 0,9879 | 0,9803 | 0,0319 | **0,9894** | **0,9971** | **0,0135** | 0,0204 | 0,0044 |
| sin marcapasos (44) | desde 1 s, ±150 ms | 100 623 | 0,9884 | 0,9802 | 0,0315 | 0,9894 | 0,9939 | 0,0167 | 0,0209 | 0,0057 |
| sin marcapasos (44) | desde 1 s, ±75 ms | 100 623 | 0,9826 | 0,9745 | 0,0430 | 0,9872 | 0,9916 | 0,0212 | | |
| sin marcapasos (44) | desde 1 s, ±50 ms | 100 623 | 0,9675 | 0,9595 | 0,0734 | 0,9837 | 0,9881 | 0,0281 | | |
| 30 del sondeo previo | **EC57**: desde 5 min, ±150 ms | 56 051 | 0,9839 | 0,9715 | 0,0449 | 0,9857 | 0,9958 | 0,0184 | 0,0232 | 0,0331 |
| 30 del sondeo previo | desde 1 s, ±75 ms | 67 172 | 0,9837 | 0,9708 | 0,0460 | 0,9761 | 0,9848 | 0,0390 | | |

`corridos` = latidos detectados a ±150 ms pero a más de 50 ms de la anotación, sobre el
total de latidos. `sin marcapasos` excluye 102, 104, 107 y 217, que es lo que reporta
casi toda la literatura (EC57 lo permite). La fila de los 30 es el subconjunto del
primer sondeo, que había dado `pt` 0,9761 / 0,9801 y `nk` 0,9837 / 0,9703 con ±75 ms
desde el inicio: la Se coincide y el PPV sube unas milésimas por excluir el flutter de
207 (`pt` tenía 318 detecciones dentro del flutter y `nk` 36) y por el conjunto de
símbolos.

**Con el criterio de EC57, `pt` (sobre el registro entero) le gana a `nk`, y por más
que con el estricto**: sin marcapasos, en Se, PPV, DER (0,0135 contra 0,0319) y
ubicación. Los errores
de ubicación se reparten distinto: los de `pt` son sobre todo de los registros con
marcapasos (217, 107 y 104 suman el 79 %: ver abajo) y sin ellos quedan en 0,44 %; los
de `nk` (2 %) son en un 88,5 % de 207, 222, 108 y 200.

### MIT-BIH: como corren en producción

Las variantes de la sección Método, con el criterio de EC57:

| conjunto | `nk` (entero) | `nk_por_bloque` | `pt` (entero) | `pt_por_lote` | `pt_reinicio` |
|---|--:|--:|--:|--:|--:|
| todos: Se / PPV | 0,989 / 0,982 | 0,989 / 0,982 | 0,990 / 0,997 | 0,986 / 0,984 | 0,993 / 0,996 |
| todos: DER | 0,0295 | 0,0296 | 0,0126 | 0,0303 | 0,0108 |
| sin marcapasos: Se / PPV | 0,988 / 0,980 | 0,988 / 0,980 | 0,989 / 0,997 | 0,985 / 0,983 | 0,992 / 0,996 |
| sin marcapasos: DER | 0,0319 | 0,0321 | **0,0135** | **0,0327** | 0,0115 |

- **El corte por bloques no le cambia nada a `nk`** (DER 0,0296 contra 0,0295; solo 108
  se mueve más de 0,005). Su umbral es local.
- **A `pt` el corte por lotes le saca toda la ventaja**: sin marcapasos, el DER pasa de
  0,0135 a 0,0327, el de `nk` en producción es 0,0321. Lo que pierde está en 124
  (DER 0,005 → 0,418), 108 (0,195 → 0,487), 121 (0,001 → 0,250), 101 (0,001 → 0,116),
  231, 202 y 117. El mecanismo es el mal enganche del arranque, que ahora se repite en
  cada lote: los errores se concentran en pocos lotes (tienen alguno 7 de los 70 lotes
  de 121, 4 de 101, 17 de 124 y 20 de 108), y en esos los 8 s de aprendizaje fijan el
  umbral sobre otra onda y el lote entero sale corrido (+266 ms, la T, en 121; −158 a
  −208 ms, antes del R, en 101, 124 y 108): los errores se reparten parejo dentro del
  lote, no solo al principio. Gana 114 (0,251 → 0,165), donde la ceguera ahora dura un
  lote.
- Con los lotes que manda el puente el resultado no depende del largo exacto; con lotes
  mucho más largos `pt` mejora. Con `detect_pt_batched(..., batch_s=...)`, sin
  marcapasos y EC57, el DER es 0,0329 con lotes de 15 s, 0,0327 con 26, 0,0235 con 60 y
  0,0142 con 300.
- `pt_reinicio` (sobre el registro entero) solo cambia de verdad 114 (0,251 → 0,151; a
  cambio, sus FP pasan de 19 a 101), y 116 recupera 6 latidos. No se midió combinado
  con el corte por lotes.

### MIT-BIH: los 5 peores de cada detector (EC57, por DER)

Los minutos cuentan desde el inicio del registro (min 0 = el primero; EC57 mira desde el
min 5). El modo de falla sale de `diagnose` (dónde caen los FP respecto del R, si cada
FN tiene una detección corrida al lado, la amplitud del QRS perdido, en qué minutos se
concentran) y se revisó registro por registro sobre la señal.

**`nk`**

| registro | Se | PPV | FN | FP | DER | modo de falla |
|---|--:|--:|--:|--:|--:|---|
| 113 | 0,9993 | 0,6128 | 1 | 951 | 0,6321 | Doble detección sobre la **onda T**, ~326 ms después de casi cada R y parejo en todo el registro. |
| 231 | 1,0000 | 0,7694 | 0 | 383 | 0,2997 | La **onda P bloqueada** del 2:1. Dispara a ~680 ms del R, al 41 % del R-R, solo en los tramos de bloqueo AV de segundo grado (`(BII`: min 5,6-9,4, 13,3-17,4 y 20,1-23,5 dentro de EC57). Verificado (ver abajo). |
| 207 | 0,7242 | 0,9957 | 439 | 5 | 0,2789 | **Pierde latidos de BRI** (QRS ancho) sin ninguna detección cerca: 329 de BRI y 107 A, en los min 5, 20-25 y 29-30. Donde sí lo marca, el R de BRI va ~66 ms antes de la anotación: 1040 corridos. |
| 108 | 0,8946 | 0,9001 | 156 | 147 | 0,2047 | En los min 28-30 el QRS es chico y negativo (−0,48 mV en el promedio de los perdidos) y marca la **onda P**, ~210 ms antes (+0,24 mV). `pt` falla igual en el mismo registro. |
| 203 | 0,9202 | 0,9909 | 198 | 21 | 0,0883 | **Pierde latidos V**: 178 de los 198 FN son V, y 169 de los FN no tienen ninguna detección a menos de 300 ms. |

**`pt`** (sobre el registro entero)

| registro | Se | PPV | FN | FP | DER | modo de falla |
|---|--:|--:|--:|--:|--:|---|
| 114 | 0,7612 | 0,9847 | 383 | 19 | 0,2506 | **Se queda ciego por tramos**: en los min 6-10 pierde casi todos los latidos (46-58 de ~60 por minuto), y parte en los min 5, 11 y 16, sin ningún disparo a menos de 300 ms. Son QRS algo más chicos (0,65 mV contra 0,81 de los detectados) en V5, el canal 0 de 114: quedan bajo el umbral adaptativo y la búsqueda hacia atrás no los recupera. |
| 108 | 0,9054 | 0,9005 | 140 | 148 | 0,1946 | La **onda P**, igual que `nk`: ~207 ms antes del R en los min 28-30. |
| 203 | 0,9601 | 0,9937 | 99 | 15 | 0,0459 | Pierde latidos (58 normales, 38 V; 84 sin ninguna detección a menos de 300 ms), de 0,61× la amplitud de los detectados. |
| 201 | 0,9625 | 1,0000 | 57 | 0 | 0,0375 | Pierde 56 latidos `a` (extrasístoles auriculares aberrantes), de 0,49× la amplitud de los detectados. |
| 105 | 0,9944 | 0,9844 | 12 | 34 | 0,0213 | Registro con mucho ruido y artefacto. 25 de los 34 FP caen 150-450 ms después de un R (la T o el ruido detrás); 10 de los 12 FN tienen una detección corrida ~180 ms antes. |

**La onda P de 231, verificada.** Si los FP son la P bloqueada de un 2:1, caen donde
caería la P siguiente a la conducida: R anterior + R-R/2 − PR, con el PR medido en el
promedio de los latidos de `(BII` (146 ms del pico de la P al R). Los 405 FP dentro de
`(BII` caen a −9 ms de esa predicción en mediana (p10…p90: −34…+9 ms; 93 % a menos de
40 ms). El promedio de la señal limpia alineado en los FP se parece a la P conducida
(correlación 0,92; 0,73 contra el QRS) y tiene su amplitud (0,25 mV pico a pico contra
0,28 de la P y 1,44 del QRS). El `.atr` no sirve de referencia: tiene solo dos `x`.

**Lo que cambia con el criterio estricto (desde 1 s, ±75 ms).** Los 5 peores eran
otros, y la diferencia es de criterio, no de detección:

- `pt` sumaba **124** (DER 0,441): marca un punto ~140 ms antes del R anotado y otro
  sobre la T, pero solo en los min 0-4, el período de aprendizaje que EC57 excluye;
  desde el min 5 da 0,9949 / 1,0000. Sobre el registro entero es una falla del
  arranque. **En producción no**: `pt_por_lote` reaprende en cada lote y 17 de los 70
  lotes de 124 salen mal enganchados (DER 0,418 en EC57).
- `pt` sumaba **107** y **104** (marcapasos, DER 0,352 y 0,222): ubica el R de los
  latidos estimulados +100 y +94 ms después de la anotación, sobre otra parte del
  complejo. A ±150 ms son TP: con EC57, 107 da 0,9944 / 1,0000 y 104 1,0000 / 1,0000,
  con 269 y 128 corridos. Es un error de ubicación, no de detección; 217 es lo mismo a
  ±50 ms (1187 corridos en EC57).
- `nk` sumaba **222** (DER 0,433): marca la **onda P** ~122 ms antes del R en el ritmo
  sinusal (min 0-8 sobre todo), donde el latido promedio tiene una P de 0,23 mV contra
  un R de 0,70. A ±150 ms son TP: con EC57 da 1,0000 / 0,9991 con 333 corridos (536
  desde el segundo 1).
- Entran 203 en `nk`, y 203, 201 y 105 en `pt`.

### NSTDB: Se / PPV por SNR (118 + 119, totales brutos, ±150 ms)

| SNR dB | ruido nk | ruido pt | limpio nk | limpio pt |
|--:|--:|--:|--:|--:|
| original | 1,000 / 1,000 | 1,000 / 1,000 | 1,000 / 1,000 | 1,000 / 1,000 |
| 24 | 1,000 / **0,982** | 1,000 / 1,000 | 1,000 / 1,000 | 1,000 / 1,000 |
| 18 | 0,999 / **0,950** | 1,000 / 0,999 | 1,000 / 1,000 | 1,000 / 1,000 |
| 12 | 0,970 / 0,851 | 0,988 / 0,930 | 1,000 / 1,000 | 1,000 / 1,000 |
| 6 | 0,900 / 0,714 | 0,923 / 0,674 | 1,000 / 1,000 | 1,000 / 1,000 |
| 0 | 0,703 / 0,549 | 0,776 / 0,510 | 1,000 / 1,000 | 1,000 / 0,999 |
| −6 | 0,524 / 0,427 | 0,600 / 0,403 | 1,000 / 1,000 | **0,756** / 0,999 |

- Con ruido leve (24 y 18 dB) `nk` ya mete FP (PPV 0,982 y 0,950; en 119 baja a 0,967
  y 0,914) y `pt` no; igual a ±75 ms. A ±50 ms se invierte: el R de `pt` se corre con el
  ruido (Se 0,957 a 24 dB y 0,919 a 18 dB) y el de `nk` no (0,999 y 0,998).
- De 12 dB para abajo los dos se degradan parecido: `pt` con algo más de Se y, desde
  6 dB, menos PPV. Eso es lo que tiene que atajar el gate de calidad, no el detector.
- En el registro a −6 dB `pt` **no se recupera en los bloques limpios**: Se 0,756 ahí,
  contra 1,000 de `nk`; el umbral que subió con el ruido tarda más que los 2 min del
  bloque limpio en volver a bajar. Como en producción (`pt_por_lote`) se recupera más,
  0,875, porque cada lote reaprende; `pt_reinicio` llega a 0,979. `nk_por_bloque` da lo
  mismo que `nk` en todas las filas.

### Chaleco: Se / PPV contra el firmware

GOOD es el 42,9 % de los 84,4 min de las 16 capturas; MARGINAL, 0 %; `GOOD sin bSQI`,
el mismo 42,9 %, con la misma región muestra a muestra en las 16.

| capturas | región | referencia | tol | latidos ref | nk Se / PPV | pt Se / PPV | desvío nk / pt (ms) |
|---|---|---|--:|--:|--:|--:|--:|
| todas (16) | GOOD | nominal | ±75 ms | 2164 | 0,998 / 0,997 | 0,731 / 0,893 | +58 / +58 |
| todas (16) | GOOD | nominal | ±50 ms | 2164 | 0,023 / 0,023 | 0,002 / 0,003 | |
| todas (16) | GOOD | por latido | ±75 ms | 2165 | 0,999 / 0,998 | 0,733 / 0,897 | +0 / +2 |
| todas (16) | GOOD | por latido | ±50 ms | 2165 | 0,998 / 0,997 | 0,732 / 0,894 | |
| todas (16) | GOOD sin bSQI | por latido | ±75 ms | 2165 | 0,999 / 0,998 | 0,733 / 0,897 | +0 / +2 |
| sin marcas (8) | GOOD | por latido | ±75 ms | 1412 | 0,999 / 0,999 | **0,963 / 0,931** | +0 / +2 |
| con marcas (8) | GOOD | por latido | ±75 ms | 753 | 0,999 / 0,996 | **0,303 / 0,735** | +0 / +2 |

Las variantes, en GOOD, referencia por latido, ±75 ms:

| capturas | latidos ref | nk | nk_por_bloque | pt | pt_por_lote | pt_reinicio | pt_sin_marcas |
|---|--:|--:|--:|--:|--:|--:|--:|
| todas (16) | 2165 | 0,999 / 0,998 | 0,999 / 0,998 | 0,733 / 0,897 | **0,877 / 0,891** | 0,958 / 0,919 | 0,807 / 0,905 |
| sin marcas (8) | 1412 | 0,999 / 0,999 | 0,999 / 0,999 | 0,963 / 0,931 | 0,945 / 0,899 | 0,963 / 0,931 | 0,963 / 0,931 |
| con marcas (8) | 753 | 0,999 / 0,996 | 0,999 / 0,996 | 0,303 / 0,735 | 0,748 / 0,873 | 0,947 / 0,897 | 0,515 / 0,826 |
| aviso_ll_ra | 201 | 1,000 / 1,000 | 1,000 / 1,000 | 0,000 / 0,000 | 0,562 / 0,673 | 0,866 / 0,760 | 0,000 / 0,000 |
| leadoff_broches | 53 | 1,000 / 1,000 | 1,000 / 1,000 | 0,264 / 0,341 | 0,755 / 0,597 | 0,755 / 0,597 | 0,264 / 0,341 |
| leadoff_final | 39 | 1,000 / 1,000 | 1,000 / 1,000 | 0,000 / — | 1,000 / 1,000 | 1,000 / 1,000 | 0,000 / — |
| leadoff_head_con_puente | 10 | 1,000 / 0,833 | 1,000 / 0,833 | 0,000 / — | 0,000 / — | 1,000 / 1,000 | 0,000 / — |
| leadoff_piel_cargador | 125 | 1,000 / 1,000 | 1,000 / 1,000 | 0,392 / 1,000 | 0,768 / 1,000 | 1,000 / 1,000 | 0,392 / 1,000 |
| loff0C_gel | 229 | 0,996 / 1,000 | 0,996 / 1,000 | 0,301 / 1,000 | 0,782 / 1,000 | 1,000 / 1,000 | 1,000 / 1,000 |

- **El retardo nominal del firmware está corrido +58 ms.** El retardo real, latido por
  latido, es `r_lag_ms` (33 ms de mediana, 32-35 ms del p5 al p95 en las capturas
  limpias) más los 160 ms del FIR: ~193 ms, no los 250 de `ml_firmware_peak_lag_ms`.
  Para el bSQI no importa (su tolerancia es de 150 ms), pero contra el tren nominal la
  puntuación a ±50 ms colapsa para los dos detectores por culpa de la referencia. Por
  eso la columna que vale es `por latido`, donde los dos quedan a 0-2 ms.
- **`pt` se queda ciego después de un evento de energía grande, marcado o no.** En
  `_classify_candidates` el nivel de señal (`spki`) solo se mueve cuando se acepta un
  latido. Un evento que la integración de Pan-Tompkins ve como un QRS enorme —el riel
  del AFE o un escalón de línea de base— se acepta, sube `spki`, y el umbral
  (`npki + 0,25 (spki − npki)`) queda por encima de todo lo que sigue; como ya no se
  acepta nada, `spki` no vuelve a bajar. No hace falta la marca: en `aviso_ll_ra` las
  últimas detecciones de `pt` caen a los 32,6-34,6 s; entre los 30 y los 35 s la señal
  cruda pasa de +47 a −367 mV, con un pico de la energía integrada 19 965 veces su
  mediana de los s 5-15, y **sin** `LEAD_OFF` ni `ADC_SATURATED` (|x| < 383 mV, el
  umbral de saturación); la primera muestra marcada recién está a los 100,4 s. En
  `leadoff_piel_cargador`, fuera de lo marcado, el p99 de |x − mediana| es 407 mV: cerca
  del riel sin quedar marcado.
- **Una guarda por flags no alcanza.** `pt_sin_marcas` arregla `loff0C_gel` (0,301 →
  1,000), donde la ceguera sí venía de lo marcado, y nada más: `aviso_ll_ra`,
  `leadoff_final` y `leadoff_head_con_puente` siguen en 0, `leadoff_piel_cargador` en
  0,392 y `leadoff_broches` en 0,264. Lo que la arregla es **reaprender el umbral**:
  `pt_reinicio` lleva las capturas con marcas de 0,303 a 0,947. En producción el corte
  por lotes ya reaprende cada ~26 s, así que lo que el umbral arrastra dura a lo sumo
  un lote; aun así `pt_por_lote` da 0,748 / 0,873 con marcas (`aviso_ll_ra` 0,562,
  `leadoff_head_con_puente` sigue en 0) y 0,877 / 0,891 en total, contra 0,999 / 0,998
  de `nk`.
- Sin marcas, `pt` falla sobre todo en `ab_router` (0,840 / 0,729 a ±50 ms): ondas T
  y disparos lejos de todo QRS, más los QRS más chicos que se le escapan (0,65× la
  amplitud de los detectados). Sacarle la red de 50 Hz antes (`quality.remove_mains`) no
  cambia el resultado: 469 → 473 aciertos sobre 555 latidos, en la captura entera. Como
  en producción, sin marcas, `pt` baja de 0,963 / 0,931 a 0,945 / 0,899.

## Lectura para la unificación

- **Sobre el registro entero y con el criterio de EC57, `pt` es mejor que `nk` en
  MIT-BIH**: sin marcapasos, Se 0,9894 contra 0,9879, PPV 0,9971 contra 0,9803, DER
  0,0135 contra 0,0319. Los errores de `nk` se concentran en pocos registros y con un
  patrón claro: toma la onda T (113) o la onda P (231, la bloqueada; 108) por un R y
  pierde QRS anchos de BRI (207) y latidos V (203). Son fallas de **qué onda reconoce
  como QRS**, no de un umbral que se queda trabado. La P de 222 a −122 ms es un error de
  ubicación, no de detección.
- **Pero `pt` no corre así en producción.** `append_beat_analysis` lo corre por lote y
  reaprende el umbral cada ~26 s; medido así, la ventaja desaparece: sin marcapasos,
  DER 0,0327 contra 0,0321 de `nk` como corre el motor. Los números de `pt` sobre el
  registro entero son de un `pt` que hoy no existe; para `/holter-metrics` valen los de
  `pt_por_lote`.
- **`pt` falla por su umbral adaptativo**: el mal enganche del aprendizaje (124 en el
  registro entero; en producción, en cualquier lote: 124, 108, 121, 101), los tramos
  donde se queda ciego (114), la recuperación lenta tras ruido (NSTDB −6 dB) y, sobre
  todo, cualquier evento de energía grande en el chaleco, que en este equipo no es un
  caso raro sino lo que pasa cada vez que un electrodo se mueve o se despega. Con
  marcapasos ubica el R en otro pico del complejo (104, 107; 217 a ±50 ms): es un error
  de ubicación, que EC57 no cuenta.
- **En el chaleco, con la señal real del equipo, `nk` es el único que no se rompe**
  (0,999 / 0,998; 0,999 / 0,996 en las capturas con marcas; lo mismo por bloques).
  Como corre en producción, `pt` da 0,877 / 0,891. La región donde se mide
  no depende de `nk`: coincide con la que eligen la Capa A y los índices espectrales.
  El límite de esta cifra es la referencia, el detector del firmware sin validar.
- **Si se quisiera conservar `pt`**, lo que le falta es reaprender el umbral cuando se
  queda ciego (`pt_reinicio`: 0,947 en las capturas con marcas, y no empeora el DER de
  MIT-BIH), no una guarda por flags, y además resolver el mal enganche de cada lote.
  `pt_reinicio` se midió sobre el registro entero; combinado con el corte por lotes no
  se midió.
- Los dos ubican el R a 0-4 ms de la anotación en la mayoría de los registros (ver el
  desvío por registro en el anexo); a ±50 ms las diferencias vienen de los mismos
  registros problemáticos.

## Anexo: salida completa de la corrida

Generada con `uv run python -m tools.physionet.evaluate --detectors --jobs 8` y
copiada sin editar salvo un nivel más en los títulos (los decimales con punto son los
del script). `nk` y `pt` como arriba; ᵖ marca los registros con marcapasos.

### MIT-BIH Arrhythmia

48 registros; 143 s de flutter excluidos. `corridos`: latidos detectados a ±150 ms pero a más de 50 ms de la anotación (error de ubicación del fiducial, no de detección).

#### Por registro, EC57 (desde 5 min, ±150 ms)

| registro | latidos | nk Se | nk PPV | nk FN | nk FP | nk corridos | pt Se | pt PPV | pt FN | pt FP | pt corridos |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| 100 | 1902 | 0.9989 | 1.0000 | 2 | 0 | 0 | 0.9995 | 1.0000 | 1 | 0 | 0 |
| 101 | 1523 | 1.0000 | 0.9993 | 0 | 1 | 1 | 1.0000 | 0.9993 | 0 | 1 | 1 |
| 102ᵖ | 1821 | 0.9995 | 1.0000 | 1 | 0 | 63 | 1.0000 | 1.0000 | 0 | 0 | 49 |
| 103 | 1729 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0.9994 | 1.0000 | 1 | 0 | 0 |
| 104ᵖ | 1857 | 0.9984 | 0.9995 | 3 | 1 | 4 | 1.0000 | 1.0000 | 0 | 0 | 128 |
| 105 | 2155 | 0.9879 | 0.9930 | 26 | 15 | 6 | 0.9944 | 0.9844 | 12 | 34 | 47 |
| 106 | 1696 | 0.9971 | 0.9953 | 5 | 8 | 0 | 0.9988 | 1.0000 | 2 | 0 | 23 |
| 107ᵖ | 1784 | 0.9994 | 1.0000 | 1 | 0 | 6 | 0.9944 | 1.0000 | 10 | 0 | 269 |
| 108 | 1480 | 0.8946 | 0.9001 | 156 | 147 | 106 | 0.9054 | 0.9005 | 140 | 148 | 16 |
| 109 | 2099 | 1.0000 | 1.0000 | 0 | 0 | 13 | 0.9976 | 1.0000 | 5 | 0 | 20 |
| 111 | 1776 | 1.0000 | 1.0000 | 0 | 0 | 6 | 0.9994 | 1.0000 | 1 | 0 | 5 |
| 112 | 2111 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 | 0 |
| 113 | 1506 | 0.9993 | 0.6128 | 1 | 951 | 0 | 0.9993 | 1.0000 | 1 | 0 | 0 |
| 114 | 1604 | 0.9988 | 0.9981 | 2 | 3 | 0 | 0.7612 | 0.9847 | 383 | 19 | 0 |
| 115 | 1637 | 1.0000 | 0.9994 | 0 | 1 | 0 | 0.9994 | 1.0000 | 1 | 0 | 0 |
| 116 | 2017 | 0.9931 | 0.9990 | 14 | 2 | 2 | 0.9871 | 0.9990 | 26 | 2 | 1 |
| 117 | 1284 | 0.9992 | 1.0000 | 1 | 0 | 0 | 0.9992 | 1.0000 | 1 | 0 | 0 |
| 118 | 1916 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 | 26 |
| 119 | 1661 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 | 0 |
| 121 | 1560 | 0.9994 | 1.0000 | 1 | 0 | 0 | 0.9987 | 1.0000 | 2 | 0 | 5 |
| 122 | 2054 | 1.0000 | 1.0000 | 0 | 0 | 1 | 1.0000 | 1.0000 | 0 | 0 | 0 |
| 123 | 1269 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0.9976 | 1.0000 | 3 | 0 | 0 |
| 124 | 1367 | 0.9861 | 0.9985 | 19 | 2 | 11 | 0.9949 | 1.0000 | 7 | 0 | 5 |
| 200 | 2168 | 0.9972 | 0.9986 | 6 | 3 | 105 | 0.9977 | 0.9995 | 5 | 1 | 11 |
| 201 | 1521 | 1.0000 | 0.9620 | 0 | 60 | 2 | 0.9625 | 1.0000 | 57 | 0 | 15 |
| 202 | 1871 | 0.9995 | 1.0000 | 1 | 0 | 0 | 0.9963 | 1.0000 | 7 | 0 | 9 |
| 203 | 2481 | 0.9202 | 0.9909 | 198 | 21 | 33 | 0.9601 | 0.9937 | 99 | 15 | 28 |
| 205 | 2201 | 0.9873 | 1.0000 | 28 | 0 | 1 | 0.9977 | 1.0000 | 5 | 0 | 1 |
| 207 | 1592 | 0.7242 | 0.9957 | 439 | 5 | 1040 | 0.9987 | 0.9981 | 2 | 3 | 3 |
| 208 | 2437 | 0.9967 | 0.9988 | 8 | 3 | 4 | 0.9918 | 0.9992 | 20 | 2 | 3 |
| 209 | 2519 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1.0000 | 0.9996 | 0 | 1 | 0 |
| 210 | 2204 | 0.9614 | 0.9991 | 85 | 2 | 2 | 0.9800 | 0.9991 | 44 | 2 | 0 |
| 212 | 2285 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0.9996 | 1.0000 | 1 | 0 | 0 |
| 213 | 2700 | 0.9978 | 1.0000 | 6 | 0 | 27 | 0.9989 | 1.0000 | 3 | 0 | 24 |
| 214 | 1879 | 0.9984 | 1.0000 | 3 | 0 | 4 | 1.0000 | 1.0000 | 0 | 0 | 94 |
| 215 | 2795 | 0.9986 | 1.0000 | 4 | 0 | 0 | 0.9986 | 1.0000 | 4 | 0 | 0 |
| 217ᵖ | 1845 | 0.9989 | 0.9995 | 2 | 1 | 1 | 0.9951 | 0.9995 | 9 | 1 | 1187 |
| 219 | 1773 | 1.0000 | 1.0000 | 0 | 0 | 1 | 0.9966 | 1.0000 | 6 | 0 | 1 |
| 220 | 1694 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 | 0 |
| 221 | 2020 | 0.9965 | 0.9975 | 7 | 5 | 1 | 0.9980 | 1.0000 | 4 | 0 | 1 |
| 222 | 2116 | 1.0000 | 0.9991 | 0 | 2 | 333 | 0.9877 | 0.9971 | 26 | 6 | 0 |
| 223 | 2199 | 1.0000 | 1.0000 | 0 | 0 | 7 | 0.9977 | 1.0000 | 5 | 0 | 0 |
| 228 | 1703 | 0.9988 | 0.9947 | 2 | 9 | 5 | 0.9935 | 0.9959 | 11 | 7 | 17 |
| 230 | 1859 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 | 0 |
| 231 | 1278 | 1.0000 | 0.7694 | 0 | 383 | 0 | 1.0000 | 1.0000 | 0 | 0 | 0 |
| 232 | 1485 | 1.0000 | 0.9738 | 0 | 40 | 0 | 0.9993 | 0.9987 | 1 | 2 | 1 |
| 233 | 2561 | 0.9984 | 1.0000 | 4 | 0 | 4 | 0.9988 | 1.0000 | 3 | 0 | 9 |
| 234 | 2291 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0.9996 | 1.0000 | 1 | 0 | 0 |

ᵖ con marcapasos.

#### Por registro, desde 1 s, ±75 ms

| registro | latidos | nk Se | nk PPV | nk FN | nk FP | pt Se | pt PPV | pt FN | pt FP |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| 100 | 2270 | 0.9996 | 1.0000 | 1 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 101 | 1863 | 0.9989 | 0.9984 | 2 | 3 | 0.9989 | 0.9979 | 2 | 4 |
| 102ᵖ | 2185 | 0.9936 | 0.9940 | 14 | 13 | 0.9941 | 0.9941 | 13 | 13 |
| 103 | 2082 | 1.0000 | 1.0000 | 0 | 0 | 0.9995 | 1.0000 | 1 | 0 |
| 104ᵖ | 2226 | 0.9942 | 0.9973 | 13 | 6 | 0.8890 | 0.8890 | 247 | 247 |
| 105 | 2570 | 0.9887 | 0.9930 | 29 | 18 | 0.9840 | 0.9757 | 41 | 63 |
| 106 | 2025 | 0.9975 | 0.9956 | 5 | 9 | 0.9990 | 1.0000 | 2 | 0 |
| 107ᵖ | 2134 | 0.9981 | 0.9986 | 4 | 3 | 0.8215 | 0.8253 | 381 | 371 |
| 108 | 1761 | 0.8881 | 0.8989 | 197 | 176 | 0.9046 | 0.8990 | 168 | 179 |
| 109 | 2528 | 0.9980 | 0.9984 | 5 | 4 | 0.9980 | 1.0000 | 5 | 0 |
| 111 | 2122 | 0.9976 | 0.9976 | 5 | 5 | 0.9976 | 0.9981 | 5 | 4 |
| 112 | 2537 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 113 | 1792 | 1.0000 | 0.6007 | 0 | 1191 | 1.0000 | 1.0000 | 0 | 0 |
| 114 | 1877 | 0.9989 | 0.9979 | 2 | 4 | 0.7906 | 0.9874 | 393 | 19 |
| 115 | 1950 | 1.0000 | 0.9995 | 0 | 1 | 1.0000 | 1.0000 | 0 | 0 |
| 116 | 2409 | 0.9942 | 0.9992 | 14 | 2 | 0.9892 | 0.9987 | 26 | 3 |
| 117 | 1533 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 118 | 2276 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 119 | 1985 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 121 | 1861 | 0.9995 | 1.0000 | 1 | 0 | 0.9984 | 0.9995 | 3 | 1 |
| 122 | 2472 | 0.9996 | 0.9996 | 1 | 1 | 1.0000 | 1.0000 | 0 | 0 |
| 123 | 1516 | 1.0000 | 1.0000 | 0 | 0 | 0.9974 | 0.9993 | 4 | 1 |
| 124 | 1617 | 0.9858 | 0.9956 | 23 | 7 | 0.8497 | 0.7451 | 243 | 470 |
| 200 | 2598 | 0.9969 | 0.9985 | 8 | 4 | 0.9985 | 0.9996 | 4 | 1 |
| 201 | 1961 | 0.9990 | 0.9693 | 2 | 62 | 0.9679 | 0.9974 | 63 | 5 |
| 202 | 2134 | 0.9995 | 1.0000 | 1 | 0 | 0.9916 | 0.9948 | 18 | 11 |
| 203 | 2978 | 0.9201 | 0.9885 | 238 | 32 | 0.9580 | 0.9906 | 125 | 27 |
| 205 | 2654 | 0.9879 | 1.0000 | 32 | 0 | 0.9981 | 1.0000 | 5 | 0 |
| 207 | 1857 | 0.7286 | 0.9833 | 504 | 23 | 0.9903 | 0.9930 | 18 | 13 |
| 208 | 2951 | 0.9959 | 0.9976 | 12 | 7 | 0.9925 | 0.9993 | 22 | 2 |
| 209 | 3003 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 0.9997 | 0 | 1 |
| 210 | 2646 | 0.9633 | 0.9992 | 97 | 2 | 0.9807 | 0.9985 | 51 | 4 |
| 212 | 2745 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 213 | 3247 | 0.9985 | 1.0000 | 5 | 0 | 0.9994 | 1.0000 | 2 | 0 |
| 214 | 2259 | 0.9978 | 0.9996 | 5 | 1 | 0.9942 | 0.9956 | 13 | 10 |
| 215 | 3359 | 0.9988 | 1.0000 | 4 | 0 | 0.9985 | 1.0000 | 5 | 0 |
| 217ᵖ | 2206 | 0.9991 | 0.9995 | 2 | 1 | 0.9787 | 0.9823 | 47 | 39 |
| 219 | 2152 | 1.0000 | 1.0000 | 0 | 0 | 0.9972 | 1.0000 | 6 | 0 |
| 220 | 2045 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 221 | 2425 | 0.9967 | 0.9979 | 8 | 5 | 0.9984 | 1.0000 | 4 | 0 |
| 222 | 2481 | 0.7840 | 0.7836 | 536 | 537 | 0.9895 | 0.9976 | 26 | 6 |
| 223 | 2603 | 0.9996 | 0.9996 | 1 | 1 | 0.9962 | 1.0000 | 10 | 0 |
| 228 | 2051 | 0.9990 | 0.9942 | 2 | 12 | 0.9912 | 0.9936 | 18 | 13 |
| 230 | 2253 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 | 1.0000 | 0 | 0 |
| 231 | 1569 | 1.0000 | 0.7920 | 0 | 412 | 1.0000 | 1.0000 | 0 | 0 |
| 232 | 1780 | 1.0000 | 0.9642 | 0 | 66 | 0.9989 | 0.9978 | 2 | 4 |
| 233 | 3075 | 0.9980 | 1.0000 | 6 | 0 | 0.9993 | 1.0000 | 2 | 0 |
| 234 | 2751 | 1.0000 | 1.0000 | 0 | 0 | 0.9996 | 1.0000 | 1 | 0 |

#### Por registro, desde 1 s, ±50 ms y desvío (detección − anotación)

| registro | nk Se | nk PPV | pt Se | pt PPV | nk desvío ms p50 (p10…p90) | pt desvío ms p50 (p10…p90) |
| --- | --: | --: | --: | --: | --: | --: |
| 100 | 0.9996 | 1.0000 | 1.0000 | 1.0000 | +0 (+0…+2) | +2 (+0…+2) |
| 101 | 0.9989 | 0.9984 | 0.9989 | 0.9979 | +0 (-2…+2) | +0 (-2…+0) |
| 102 | 0.9652 | 0.9657 | 0.9753 | 0.9753 | +2 (+0…+2) | +0 (-2…+2) |
| 103 | 1.0000 | 1.0000 | 0.9995 | 1.0000 | +2 (+0…+2) | +2 (+0…+4) |
| 104 | 0.9933 | 0.9964 | 0.8823 | 0.8823 | -2 (-4…+2) | -2 (-6…+2) |
| 105 | 0.9875 | 0.9918 | 0.9770 | 0.9688 | +0 (-2…+2) | +0 (-2…+2) |
| 106 | 0.9975 | 0.9956 | 0.9862 | 0.9871 | +0 (-2…+2) | +2 (+0…+4) |
| 107 | 0.9963 | 0.9967 | 0.8196 | 0.8234 | +0 (-2…+0) | -4 (-6…-2) |
| 108 | 0.7723 | 0.7816 | 0.8949 | 0.8894 | -2 (-52…+2) | +2 (-34…+6) |
| 109 | 0.9945 | 0.9949 | 0.9893 | 0.9913 | -2 (-4…+0) | +0 (-4…+0) |
| 111 | 0.9972 | 0.9972 | 0.9972 | 0.9976 | -2 (-4…+0) | -2 (-4…+0) |
| 112 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +0 (+0…+2) | +2 (+0…+4) |
| 113 | 1.0000 | 0.6007 | 1.0000 | 1.0000 | +2 (+0…+2) | +2 (+0…+2) |
| 114 | 0.9989 | 0.9979 | 0.7906 | 0.9874 | +12 (+8…+18) | +14 (-2…+34) |
| 115 | 1.0000 | 0.9995 | 1.0000 | 1.0000 | +2 (+0…+4) | +4 (+2…+4) |
| 116 | 0.9934 | 0.9983 | 0.9892 | 0.9987 | +2 (+0…+4) | +4 (+2…+6) |
| 117 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | -22 (-30…-12) | -2 (-16…+4) |
| 118 | 0.9996 | 0.9996 | 0.9864 | 0.9864 | +0 (+0…+2) | +4 (+2…+6) |
| 119 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +2 (+0…+8) | +2 (+0…+4) |
| 121 | 0.9995 | 1.0000 | 0.9962 | 0.9973 | +0 (+0…+2) | +2 (+0…+4) |
| 122 | 0.9996 | 0.9996 | 1.0000 | 1.0000 | +2 (+0…+4) | +4 (+2…+4) |
| 123 | 1.0000 | 1.0000 | 0.9974 | 0.9993 | +2 (+0…+4) | +4 (+2…+4) |
| 124 | 0.9814 | 0.9913 | 0.8485 | 0.7440 | +0 (-2…+2) | +0 (-2…+2) |
| 200 | 0.9546 | 0.9561 | 0.9927 | 0.9938 | +2 (-46…+4) | +4 (-2…+6) |
| 201 | 0.9990 | 0.9693 | 0.9628 | 0.9921 | +2 (+0…+2) | +4 (+2…+30) |
| 202 | 0.9995 | 1.0000 | 0.9916 | 0.9948 | +2 (+0…+2) | +2 (+2…+4) |
| 203 | 0.9080 | 0.9755 | 0.9490 | 0.9812 | +0 (-2…+2) | +0 (-2…+2) |
| 205 | 0.9876 | 0.9996 | 0.9977 | 0.9996 | +2 (+0…+2) | +2 (+0…+2) |
| 207 | 0.1422 | 0.1919 | 0.9849 | 0.9876 | -66 (-70…+0) | +0 (-2…+2) |
| 208 | 0.9959 | 0.9976 | 0.9915 | 0.9983 | +2 (+0…+12) | +2 (+0…+12) |
| 209 | 1.0000 | 1.0000 | 1.0000 | 0.9997 | +2 (+0…+2) | +2 (+2…+4) |
| 210 | 0.9626 | 0.9984 | 0.9807 | 0.9985 | +0 (+0…+2) | +2 (+0…+4) |
| 212 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +0 (+0…+2) | +2 (+0…+4) |
| 213 | 0.9889 | 0.9904 | 0.9908 | 0.9914 | +0 (+0…+2) | +2 (+0…+2) |
| 214 | 0.9951 | 0.9969 | 0.9473 | 0.9486 | +2 (+0…+4) | +4 (+2…+6) |
| 215 | 0.9988 | 1.0000 | 0.9985 | 1.0000 | +2 (+0…+4) | +4 (+2…+4) |
| 217 | 0.9986 | 0.9991 | 0.3336 | 0.3348 | -16 (-20…+2) | +62 (+0…+70) |
| 219 | 0.9995 | 0.9995 | 0.9967 | 0.9995 | +2 (+0…+2) | +2 (+0…+4) |
| 220 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +2 (+0…+4) | +4 (+2…+6) |
| 221 | 0.9963 | 0.9975 | 0.9979 | 0.9996 | +2 (+0…+4) | +2 (+0…+4) |
| 222 | 0.7840 | 0.7836 | 0.9895 | 0.9976 | +0 (+0…+2) | +2 (+0…+2) |
| 223 | 0.9965 | 0.9965 | 0.9962 | 1.0000 | +2 (-2…+2) | +4 (+0…+6) |
| 228 | 0.9941 | 0.9893 | 0.9829 | 0.9853 | +2 (-2…+2) | +2 (+0…+4) |
| 230 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +2 (+0…+4) | +4 (+2…+6) |
| 231 | 1.0000 | 0.7920 | 1.0000 | 1.0000 | +0 (-2…+2) | +0 (-2…+2) |
| 232 | 1.0000 | 0.9642 | 0.9983 | 0.9972 | +2 (+0…+2) | +4 (+2…+4) |
| 233 | 0.9967 | 0.9987 | 0.9961 | 0.9967 | +0 (-42…+2) | +2 (+0…+6) |
| 234 | 1.0000 | 1.0000 | 0.9996 | 1.0000 | +2 (+0…+2) | +2 (+0…+2) |

#### Totales brutos (Σ TP / Σ FN / Σ FP sobre los registros)

`corridos` = Σ latidos corridos / latidos, solo a ±150 ms. EC57 es lo que hace `bxb` sin opciones; los demás criterios son más estrictos.

| conjunto | criterio | registros | latidos | nk Se | nk PPV | nk DER | pt Se | pt PPV | pt DER | nk corridos | pt corridos |
| --- | --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| todos | EC57: desde 5 min, ±150 ms | 48 | 91285 | 0.9888 | 0.9819 | 0.0295 | 0.9900 | 0.9973 | 0.0126 | 0.0196 | 0.0219 |
| todos | desde 1 s, ±150 ms | 48 | 109374 | 0.9892 | 0.9818 | 0.0291 | 0.9901 | 0.9943 | 0.0156 | 0.0201 | 0.0249 |
| todos | desde 1 s, ±75 ms | 48 | 109374 | 0.9837 | 0.9763 | 0.0401 | 0.9819 | 0.9861 | 0.0319 |  |  |
| todos | desde 1 s, ±50 ms | 48 | 109374 | 0.9692 | 0.9619 | 0.0693 | 0.9652 | 0.9693 | 0.0654 |  |  |
| sin marcapasos | EC57: desde 5 min, ±150 ms | 44 | 83978 | 0.9879 | 0.9803 | 0.0319 | 0.9894 | 0.9971 | 0.0135 | 0.0204 | 0.0044 |
| sin marcapasos | desde 1 s, ±150 ms | 44 | 100623 | 0.9884 | 0.9802 | 0.0315 | 0.9894 | 0.9939 | 0.0167 | 0.0209 | 0.0057 |
| sin marcapasos | desde 1 s, ±75 ms | 44 | 100623 | 0.9826 | 0.9745 | 0.0430 | 0.9872 | 0.9916 | 0.0212 |  |  |
| sin marcapasos | desde 1 s, ±50 ms | 44 | 100623 | 0.9675 | 0.9595 | 0.0734 | 0.9837 | 0.9881 | 0.0281 |  |  |
| 30 del sondeo previo | EC57: desde 5 min, ±150 ms | 30 | 56051 | 0.9839 | 0.9715 | 0.0449 | 0.9857 | 0.9958 | 0.0184 | 0.0232 | 0.0331 |
| 30 del sondeo previo | desde 1 s, ±150 ms | 30 | 67172 | 0.9845 | 0.9716 | 0.0443 | 0.9871 | 0.9959 | 0.0169 | 0.0221 | 0.0360 |
| 30 del sondeo previo | desde 1 s, ±75 ms | 30 | 67172 | 0.9837 | 0.9708 | 0.0460 | 0.9761 | 0.9848 | 0.0390 |  |  |
| 30 del sondeo previo | desde 1 s, ±50 ms | 30 | 67172 | 0.9624 | 0.9498 | 0.0885 | 0.9511 | 0.9596 | 0.0889 |  |  |

#### Los 5 peores de `nk`, EC57: desde 5 min, ±150 ms (por DER = (FN + FP) / latidos)

| registro | Se | PPV | FN | FP | DER | modo de falla (automático) |
| --- | --: | --: | --: | --: | --: | --- |
| 113 | 0.9993 | 0.6128 | 1 | 951 | 0.6321 | detecta la onda T (~326 ms después del R): 951/951 FP |
| 231 | 1.0000 | 0.7694 | 0 | 383 | 0.2997 | FP lejos de todo QRS (~678 ms después del R): 362/383 FP; min 5-8, 13-16, 20-23 |
| 207 | 0.7242 | 0.9957 | 439 | 5 | 0.2789 | pierde latidos sin detección cerca (BRI 329, A 107); min 5, 20-25, 29-30 |
| 108 | 0.8946 | 0.9001 | 156 | 147 | 0.2047 | R corrido -210 ms de la anotación (137/156 FN con su FP al lado; normales 150, V 6); min 28-30 |
| 203 | 0.9202 | 0.9909 | 198 | 21 | 0.0883 | pierde latidos sin detección cerca (V 178, normales 19) |

#### Los 5 peores de `pt`, EC57: desde 5 min, ±150 ms (por DER = (FN + FP) / latidos)

| registro | Se | PPV | FN | FP | DER | modo de falla (automático) |
| --- | --: | --: | --: | --: | --: | --- |
| 114 | 0.7612 | 0.9847 | 383 | 19 | 0.2506 | pierde latidos sin detección cerca (normales 382, A 1); min 5-11, 16 |
| 108 | 0.9054 | 0.9005 | 140 | 148 | 0.1946 | R corrido -207 ms de la anotación (134/140 FN con su FP al lado; normales 139, A 1); min 28-30 |
| 203 | 0.9601 | 0.9937 | 99 | 15 | 0.0459 | pierde latidos sin detección cerca (normales 58, V 38); QRS perdidos de 0.61× la amplitud de los detectados |
| 201 | 0.9625 | 1.0000 | 57 | 0 | 0.0375 | pierde latidos sin detección cerca (a 56, normales 1); QRS perdidos de 0.49× la amplitud de los detectados |
| 105 | 0.9944 | 0.9844 | 12 | 34 | 0.0213 | R corrido -182 ms de la anotación (10/12 FN con su FP al lado; normales 12) |

#### Los 5 peores de `nk`, desde 1 s, ±75 ms (por DER = (FN + FP) / latidos)

| registro | Se | PPV | FN | FP | DER | modo de falla (automático) |
| --- | --: | --: | --: | --: | --: | --- |
| 113 | 1.0000 | 0.6007 | 0 | 1191 | 0.6646 | detecta la onda T (~328 ms después del R): 1191/1191 FP |
| 222 | 0.7840 | 0.7836 | 536 | 537 | 0.4325 | R corrido -122 ms de la anotación (536/536 FN con su FP al lado; normales 536); min 0-8, 13, 18, 25 |
| 207 | 0.7286 | 0.9833 | 504 | 23 | 0.2838 | pierde latidos sin detección cerca (BRI 359, A 106); min 2, 4-6, 20-25, 29-30 |
| 231 | 1.0000 | 0.7920 | 0 | 412 | 0.2626 | FP lejos de todo QRS (~678 ms después del R): 391/412 FP; min 5-8, 13-16, 20-23 |
| 108 | 0.8881 | 0.8989 | 197 | 176 | 0.2118 | R corrido -210 ms de la anotación (164/197 FN con su FP al lado; normales 191, V 6); min 0-1, 28-30 |

#### Los 5 peores de `pt`, desde 1 s, ±75 ms (por DER = (FN + FP) / latidos)

| registro | Se | PPV | FN | FP | DER | modo de falla (automático) |
| --- | --: | --: | --: | --: | --: | --- |
| 124 | 0.8497 | 0.7451 | 243 | 470 | 0.4409 | R corrido -138 ms de la anotación (236/243 FN con su FP al lado; BRD 233, V 10); min 0-4 |
| 107 | 0.8215 | 0.8253 | 381 | 371 | 0.3524 | marcapasos; R corrido +100 ms de la anotación (371/381 FN con su FP al lado; estimulados 370, V 11); min 0-1, 4, 9, 21, 23 |
| 104 | 0.8890 | 0.8890 | 247 | 247 | 0.2219 | marcapasos; R corrido +94 ms de la anotación (246/247 FN con su FP al lado; estimulados 236, fusión de MP 11); min 0, 2 |
| 114 | 0.7906 | 0.9874 | 393 | 19 | 0.2195 | pierde latidos sin detección cerca (normales 392, A 1); min 5-11, 16 |
| 108 | 0.9046 | 0.8990 | 168 | 179 | 0.1970 | R corrido -206 ms de la anotación (162/168 FN con su FP al lado; normales 167, A 1); min 0, 28-30 |

#### Variantes

- nk_por_bloque = `nk` como en producción: bloques de 300 s con 60 s de contexto y 30 s de lookahead
- pt_por_lote = `pt` como en producción: por lotes de 26 s con 30 s de contexto a cada lado, reaprendiendo el umbral en cada uno
- pt_reinicio = `pt` sobre el registro entero, que vuelve a arrancar tras 5 s sin latidos (no está en producción)

| conjunto | criterio | latidos | nk DER | nk_por_bloque DER | pt DER | pt_por_lote DER | pt_reinicio DER |
| --- | --- | --: | --: | --: | --: | --: | --: |
| todos | EC57: desde 5 min, ±150 ms | 91285 | 0.0295 | 0.0296 | 0.0126 | 0.0303 | 0.0108 |
| todos | desde 1 s, ±150 ms | 109374 | 0.0291 | 0.0297 | 0.0156 | 0.0315 | 0.0140 |
| todos | desde 1 s, ±75 ms | 109374 | 0.0401 | 0.0408 | 0.0319 | 0.0497 | 0.0304 |
| todos | desde 1 s, ±50 ms | 109374 | 0.0693 | 0.0696 | 0.0654 | 0.0832 | 0.0639 |
| sin marcapasos | EC57: desde 5 min, ±150 ms | 83978 | 0.0319 | 0.0321 | 0.0135 | 0.0327 | 0.0115 |
| sin marcapasos | desde 1 s, ±150 ms | 100623 | 0.0315 | 0.0321 | 0.0167 | 0.0340 | 0.0150 |
| sin marcapasos | desde 1 s, ±75 ms | 100623 | 0.0430 | 0.0437 | 0.0212 | 0.0405 | 0.0195 |
| sin marcapasos | desde 1 s, ±50 ms | 100623 | 0.0734 | 0.0737 | 0.0281 | 0.0475 | 0.0265 |

Se / PPV de las mismas filas:

| conjunto | criterio | nk | nk_por_bloque | pt | pt_por_lote | pt_reinicio |
| --- | --- | --: | --: | --: | --: | --: |
| todos | EC57: desde 5 min, ±150 ms | 0.989 / 0.982 | 0.989 / 0.982 | 0.990 / 0.997 | 0.986 / 0.984 | 0.993 / 0.996 |
| todos | desde 1 s, ±150 ms | 0.989 / 0.982 | 0.989 / 0.981 | 0.990 / 0.994 | 0.986 / 0.983 | 0.992 / 0.994 |
| todos | desde 1 s, ±75 ms | 0.984 / 0.976 | 0.983 / 0.976 | 0.982 / 0.986 | 0.976 / 0.974 | 0.984 / 0.985 |
| todos | desde 1 s, ±50 ms | 0.969 / 0.962 | 0.969 / 0.962 | 0.965 / 0.969 | 0.960 / 0.957 | 0.967 / 0.969 |
| sin marcapasos | EC57: desde 5 min, ±150 ms | 0.988 / 0.980 | 0.988 / 0.980 | 0.989 / 0.997 | 0.985 / 0.983 | 0.992 / 0.996 |
| sin marcapasos | desde 1 s, ±150 ms | 0.988 / 0.980 | 0.988 / 0.980 | 0.989 / 0.994 | 0.984 / 0.982 | 0.992 / 0.993 |
| sin marcapasos | desde 1 s, ±75 ms | 0.983 / 0.975 | 0.982 / 0.974 | 0.987 / 0.992 | 0.981 / 0.978 | 0.990 / 0.991 |
| sin marcapasos | desde 1 s, ±50 ms | 0.967 / 0.959 | 0.967 / 0.959 | 0.984 / 0.988 | 0.978 / 0.975 | 0.986 / 0.987 |

DER de `nk` → `nk_por_bloque` en EC57, donde cambia más de 0,005: 108 0.205 → 0.211.

DER de `pt` → `pt_por_lote` en EC57, donde cambia más de 0,005: 101 0.001 → 0.116; 108 0.195 → 0.487; 114 0.251 → 0.165; 117 0.001 → 0.027; 121 0.001 → 0.250; 124 0.005 → 0.418; 202 0.004 → 0.047; 231 0.000 → 0.062.

DER de `pt` → `pt_reinicio` en EC57, donde cambia más de 0,005: 114 0.251 → 0.151.

### NSTDB (ruido de movimiento de electrodo, canal 0)

Se / PPV a ±150 ms, la ventana de EC57. `ruido` = los bloques de 2 min con ruido sumado (empiezan a los 5 min, así que caen en la región de EC57); `limpio` = el resto (los 5 min iniciales y los bloques alternos). La fila `limpio` es el registro original de `mitdb`, puntuado sobre los mismos bloques.

| registro | SNR dB | ruido nk | ruido pt | limpio nk | limpio pt |
| --- | --: | --: | --: | --: | --: |
| 118 | limpio | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 118e24 | 24 | 1.000 / 0.996 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 118e18 | 18 | 0.999 / 0.985 | 1.000 / 0.999 | 1.000 / 1.000 | 1.000 / 1.000 |
| 118e12 | 12 | 0.965 / 0.892 | 0.988 / 0.953 | 1.000 / 1.000 | 1.000 / 1.000 |
| 118e06 | 06 | 0.882 / 0.736 | 0.936 / 0.723 | 1.000 / 1.000 | 1.000 / 1.000 |
| 118e00 | 00 | 0.681 / 0.563 | 0.805 / 0.565 | 1.000 / 1.000 | 1.000 / 1.000 |
| 118e_6 | −6 | 0.512 / 0.447 | 0.637 / 0.447 | 0.999 / 1.000 | 0.796 / 0.999 |
| 119 | limpio | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 119e24 | 24 | 1.000 / 0.967 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 119e18 | 18 | 1.000 / 0.914 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 119e12 | 12 | 0.977 / 0.810 | 0.988 / 0.905 | 1.000 / 1.000 | 1.000 / 0.999 |
| 119e06 | 06 | 0.921 / 0.692 | 0.909 / 0.625 | 1.000 / 1.000 | 1.000 / 1.000 |
| 119e00 | 00 | 0.728 / 0.535 | 0.743 / 0.456 | 1.000 / 1.000 | 1.000 / 0.998 |
| 119e_6 | −6 | 0.538 / 0.407 | 0.558 / 0.358 | 1.000 / 1.000 | 0.709 / 0.999 |

#### Totales por SNR (118 + 119, Σ TP / FN / FP)

| SNR dB | tol | ruido nk | ruido pt | limpio nk | limpio pt |
| --- | --: | --: | --: | --: | --: |
| limpio | ±150 ms | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| limpio | ±75 ms | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| limpio | ±50 ms | 1.000 / 1.000 | 0.999 / 0.999 | 1.000 / 1.000 | 0.988 / 0.988 |
| 24 | ±150 ms | 1.000 / 0.982 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 24 | ±75 ms | 1.000 / 0.982 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 24 | ±50 ms | 0.999 / 0.981 | 0.957 / 0.957 | 0.998 / 0.998 | 0.986 / 0.986 |
| 18 | ±150 ms | 0.999 / 0.950 | 1.000 / 0.999 | 1.000 / 1.000 | 1.000 / 1.000 |
| 18 | ±75 ms | 0.999 / 0.950 | 1.000 / 0.999 | 1.000 / 1.000 | 1.000 / 1.000 |
| 18 | ±50 ms | 0.998 / 0.949 | 0.919 / 0.919 | 0.998 / 0.998 | 0.987 / 0.987 |
| 12 | ±150 ms | 0.970 / 0.851 | 0.988 / 0.930 | 1.000 / 1.000 | 1.000 / 1.000 |
| 12 | ±75 ms | 0.958 / 0.841 | 0.975 / 0.917 | 1.000 / 1.000 | 1.000 / 0.999 |
| 12 | ±50 ms | 0.957 / 0.840 | 0.865 / 0.814 | 0.998 / 0.998 | 0.987 / 0.986 |
| 06 | ±150 ms | 0.900 / 0.714 | 0.923 / 0.674 | 1.000 / 1.000 | 1.000 / 1.000 |
| 06 | ±75 ms | 0.816 / 0.647 | 0.825 / 0.602 | 1.000 / 1.000 | 1.000 / 1.000 |
| 06 | ±50 ms | 0.800 / 0.635 | 0.710 / 0.519 | 0.998 / 0.998 | 0.986 / 0.986 |
| 00 | ±150 ms | 0.703 / 0.549 | 0.776 / 0.510 | 1.000 / 1.000 | 1.000 / 0.999 |
| 00 | ±75 ms | 0.549 / 0.429 | 0.580 / 0.381 | 1.000 / 1.000 | 1.000 / 0.999 |
| 00 | ±50 ms | 0.518 / 0.405 | 0.469 / 0.308 | 0.998 / 0.998 | 0.986 / 0.985 |
| −6 | ±150 ms | 0.524 / 0.427 | 0.600 / 0.403 | 1.000 / 1.000 | 0.756 / 0.999 |
| −6 | ±75 ms | 0.326 / 0.265 | 0.357 / 0.240 | 1.000 / 1.000 | 0.755 / 0.998 |
| −6 | ±50 ms | 0.277 / 0.226 | 0.277 / 0.187 | 0.998 / 0.998 | 0.742 / 0.981 |

#### Variantes por SNR (±150 ms)

| SNR dB | ruido nk | ruido nk_por_bloque | ruido pt | ruido pt_por_lote | ruido pt_reinicio | limpio nk | limpio nk_por_bloque | limpio pt | limpio pt_por_lote | limpio pt_reinicio |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| limpio | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 24 | 1.000 / 0.982 | 1.000 / 0.982 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 18 | 0.999 / 0.950 | 0.999 / 0.949 | 1.000 / 0.999 | 1.000 / 0.999 | 1.000 / 0.999 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 12 | 0.970 / 0.851 | 0.970 / 0.850 | 0.988 / 0.930 | 0.988 / 0.929 | 0.988 / 0.930 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 06 | 0.900 / 0.714 | 0.898 / 0.710 | 0.923 / 0.674 | 0.923 / 0.674 | 0.923 / 0.674 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 00 | 0.703 / 0.549 | 0.707 / 0.549 | 0.776 / 0.510 | 0.776 / 0.510 | 0.776 / 0.510 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 0.999 | 1.000 / 0.999 | 1.000 / 0.999 |
| −6 | 0.524 / 0.427 | 0.526 / 0.426 | 0.600 / 0.403 | 0.608 / 0.403 | 0.619 / 0.407 | 1.000 / 1.000 | 1.000 / 1.000 | 0.756 / 0.999 | 0.875 / 0.999 | 0.979 / 0.999 |

### Capturas del chaleco (canal 2) contra el firmware

Se / PPV dentro de las ventanas GOOD del gate. `nominal` = tren de producción (retardo fijo de 250 ms); `por latido` = `r_lag_ms` + `fir_delay` de cada latido. `GOOD sin bSQI` = el gate con el firmware contra sí mismo, que no depende de ningún candidato. `marcadas` = muestras con `LEAD_OFF` o `ADC_SATURATED`.

| captura | min | good % | marginal % | good sin bSQI % | marcadas % | latidos ref | nominal ±75 nk | nominal ±75 pt | por latido ±50 nk | por latido ±50 pt |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| ab_cargador_vecino | 5.8 | 29 | 0 | 29 | 22 | 96 | 0.990 / 0.979 | 0.990 / 0.990 | 1.000 / 0.990 | 1.000 / 1.000 |
| ab_router | 8.0 | 65 | 0 | 65 | 0 | 323 | 0.997 / 0.997 | 0.845 / 0.732 | 0.997 / 0.997 | 0.840 / 0.729 |
| ab_tapa_router | 5.6 | 0 | 0 | 0 | 0 | 0 | — | — | — | — |
| aviso_ll_ra | 5.9 | 58 | 0 | 58 | 15 | 201 | 1.000 / 1.000 | 0.000 / 0.000 | 1.000 / 1.000 | 0.000 / 0.000 |
| gel_limpia | 4.0 | 100 | 0 | 100 | 0 | 234 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| gel_reposo | 4.0 | 92 | 0 | 92 | 0 | 201 | 0.990 / 0.990 | 0.975 / 0.975 | 0.990 / 0.990 | 0.980 / 0.980 |
| leadoff_broches | 2.7 | 37 | 0 | 37 | 27 | 53 | 1.000 / 1.000 | 0.264 / 0.341 | 1.000 / 1.000 | 0.264 / 0.341 |
| leadoff_final | 6.1 | 11 | 0 | 11 | 27 | 39 | 1.000 / 1.000 | 0.000 / — | 1.000 / 1.000 | 0.000 / — |
| leadoff_head_con_puente | 7.1 | 2 | 0 | 2 | 34 | 10 | 1.000 / 0.833 | 0.000 / — | 1.000 / 0.833 | 0.000 / — |
| leadoff_piel_cargador | 7.0 | 31 | 0 | 31 | 24 | 125 | 1.000 / 1.000 | 0.392 / 1.000 | 1.000 / 1.000 | 0.392 / 1.000 |
| loff0C_gel | 5.5 | 70 | 0 | 70 | 19 | 229 | 0.996 / 1.000 | 0.297 / 0.986 | 0.996 / 1.000 | 0.301 / 1.000 |
| loff0C_seco_saturada | 6.2 | 0 | 0 | 0 | 100 | 0 | — | — | — | — |
| movimiento_con_puente | 3.3 | 0 | 0 | 0 | 0 | 0 | — | — | — | — |
| seco_ajustado | 5.0 | 90 | 0 | 90 | 0 | 281 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| seco_ajustado_20260928 | 2.2 | 55 | 0 | 55 | 0 | 75 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| seco_limpia | 6.0 | 80 | 0 | 80 | 0 | 297 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |

16 capturas, 84.4 min: GOOD 42.9 %, MARGINAL 0.0 %, GOOD sin bSQI 42.9 % del tiempo. La región GOOD es idéntica, muestra a muestra, a la de GOOD sin bSQI en 16 de 16 capturas.

#### Totales brutos

| capturas | región | referencia | tol | latidos ref | nk Se / PPV | pt Se / PPV | nk desvío ms | pt desvío ms |
| --- | --- | --- | --: | --: | --: | --: | --: | --: |
| todas (16) | GOOD | nominal | ±75 ms | 2164 | 0.998 / 0.997 | 0.731 / 0.893 | +58 | +58 |
| todas (16) | GOOD | nominal | ±50 ms | 2164 | 0.023 / 0.023 | 0.002 / 0.003 |  |  |
| todas (16) | GOOD | por latido | ±75 ms | 2165 | 0.999 / 0.998 | 0.733 / 0.897 | +0 | +2 |
| todas (16) | GOOD | por latido | ±50 ms | 2165 | 0.998 / 0.997 | 0.732 / 0.894 |  |  |
| todas (16) | MARGINAL | por latido | ±75 ms | 0 | — | — | — | — |
| todas (16) | MARGINAL | por latido | ±50 ms | 0 | — | — |  |  |
| todas (16) | GOOD sin bSQI | por latido | ±75 ms | 2165 | 0.999 / 0.998 | 0.733 / 0.897 | +0 | +2 |
| todas (16) | GOOD sin bSQI | por latido | ±50 ms | 2165 | 0.998 / 0.997 | 0.732 / 0.894 |  |  |
| sin marcas (8) | GOOD | por latido | ±75 ms | 1412 | 0.999 / 0.999 | 0.963 / 0.931 | +0 | +2 |
| sin marcas (8) | GOOD | por latido | ±50 ms | 1412 | 0.998 / 0.998 | 0.960 / 0.928 |  |  |
| con marcas (8) | GOOD | por latido | ±75 ms | 753 | 0.999 / 0.996 | 0.303 / 0.735 | +0 | +2 |
| con marcas (8) | GOOD | por latido | ±50 ms | 753 | 0.999 / 0.996 | 0.303 / 0.735 |  |  |

El desvío (detección − referencia, a ±75 ms) es la mediana de las medianas por captura.

#### Variantes

GOOD, referencia por latido, ±75 ms. `nk_por_bloque` y `pt_por_lote` son los dos detectores como corren en producción (bloques de 300 s; lotes de 26 s con 30 s de contexto a cada lado); `pt_reinicio` vuelve a arrancar tras 5 s sin latidos; `pt_sin_marcas` corre sobre la señal con lo marcado (±0,5 s) reemplazado por una recta. Las dos últimas no están en producción.

| capturas | latidos ref | nk | nk_por_bloque | pt | pt_por_lote | pt_reinicio | pt_sin_marcas |
| --- | --: | --: | --: | --: | --: | --: | --: |
| todas (16) | 2165 | 0.999 / 0.998 | 0.999 / 0.998 | 0.733 / 0.897 | 0.877 / 0.891 | 0.958 / 0.919 | 0.807 / 0.905 |
| sin marcas (8) | 1412 | 0.999 / 0.999 | 0.999 / 0.999 | 0.963 / 0.931 | 0.945 / 0.899 | 0.963 / 0.931 | 0.963 / 0.931 |
| con marcas (8) | 753 | 0.999 / 0.996 | 0.999 / 0.996 | 0.303 / 0.735 | 0.748 / 0.873 | 0.947 / 0.897 | 0.515 / 0.826 |
| ab_cargador_vecino | 96 | 1.000 / 0.990 | 1.000 / 0.990 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| aviso_ll_ra | 201 | 1.000 / 1.000 | 1.000 / 1.000 | 0.000 / 0.000 | 0.562 / 0.673 | 0.866 / 0.760 | 0.000 / 0.000 |
| leadoff_broches | 53 | 1.000 / 1.000 | 1.000 / 1.000 | 0.264 / 0.341 | 0.755 / 0.597 | 0.755 / 0.597 | 0.264 / 0.341 |
| leadoff_final | 39 | 1.000 / 1.000 | 1.000 / 1.000 | 0.000 / — | 1.000 / 1.000 | 1.000 / 1.000 | 0.000 / — |
| leadoff_head_con_puente | 10 | 1.000 / 0.833 | 1.000 / 0.833 | 0.000 / — | 0.000 / — | 1.000 / 1.000 | 0.000 / — |
| leadoff_piel_cargador | 125 | 1.000 / 1.000 | 1.000 / 1.000 | 0.392 / 1.000 | 0.768 / 1.000 | 1.000 / 1.000 | 0.392 / 1.000 |
| loff0C_gel | 229 | 0.996 / 1.000 | 0.996 / 1.000 | 0.301 / 1.000 | 0.782 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
