# Evaluación contra registros reales de PhysioNet

Los datos **no se commitean**: `data/` está en el `.gitignore` de la raíz. Hay que
bajarlos antes de correr la evaluación.

## Descarga

```bash
cd back
uv run --with wfdb python -m tools.physionet.evaluate --download            # Etapa 2
uv run --with wfdb python -m tools.physionet.evaluate --download --stage1   # Etapa 1
```

Se baja a `back/tools/physionet/data/`, archivo por archivo: PhysioNet corta
descargas largas con un 502 cada tanto, y el script reintenta solo lo que falta.

| Base | Qué es | Para qué se usa acá |
|---|---|---|
| `mitdb` | MIT-BIH Arrhythmia Database, 48 registros de 30 min a 360 Hz, con **cada latido anotado** por dos cardiólogos | Etapa 2: ¿los latidos que el motor marca como atípicos son los que están anotados como no-normales? |
| `nstdb` | MIT-BIH Noise Stress Test Database: el registro 118 con ruido **real** de electrodo sumado a SNR conocida (24 a −6 dB) | Etapa 1: ¿el gate rechaza el ruido y **no** rechaza la señal limpia? |
| `qtdb` | QT Database: 105 registros de 15 min a 250 Hz, dos derivaciones, con onset, pico y offset del QRS y fin de T **marcados a mano** en 30 a 50 latidos por registro | Etapa 3 (`--qtdb`): ¿los intervalos que mide el motor (QT, QTc, QRS, amplitud R) son los que marcó el cardiólogo? Ver la última sección |

El ruido de `nstdb` no cubre el registro entero: los primeros 5 min quedan
limpios y después se alternan bloques de 2 min con ruido y 2 min sin, o sea un
**43,3 % contaminado**. Por eso `--stage1` no reporta solo cuánto rechaza el gate
sino *dónde*: un gate que descarte el 42 % al azar da el mismo porcentaje y no
sirve para nada.

## Licencia y cita

Las tres bases se distribuyen en PhysioNet bajo **Open Data Commons Attribution
License v1.0 (ODC-By 1.0)**. Verificar la licencia en la página del registro al
momento de bajar: PhysioNet la puede cambiar por versión.

> Moody GB, Mark RG. The impact of the MIT-BIH Arrhythmia Database.
> *IEEE Eng in Med and Biol* 20(3):45-50 (2001).
>
> Moody GB, Muldrow WE, Mark RG. A noise stress test for arrhythmia detectors.
> *Computers in Cardiology* 11:381-384 (1984).
>
> Laguna P, Mark RG, Goldberger AL, Moody GB. A database for evaluation of
> algorithms for measurement of QT and other waveform intervals in the ECG.
> *Computers in Cardiology* 24:673-676 (1997).
>
> Goldberger AL, Amaral LAN, Glass L, et al. PhysioBank, PhysioToolkit, and
> PhysioNet. *Circulation* 101(23):e215-e220 (2000).

## Qué se puede y qué no se puede concluir

**Se puede** calibrar umbrales y comparar con un baseline. El detector es **no
supervisado**: nunca se entrena con estas etiquetas, solo se las usa para medir
después. Por eso reportar sobre el mismo dataset es válido acá y no lo sería en
un clasificador entrenado.

**No se puede** afirmar que estos números se trasladan a nuestro hardware. Los
registros de PhysioNet vienen de **electrodos de gel colocados por un técnico**;
nuestro dispositivo usa **electrodos secos en un chaleco textil que se coloca el
paciente**. La relación señal-ruido y los modos de falla son distintos. Estos
datasets alcanzan para calibrar, no para validar.

Dos consecuencias concretas que el script ya aplica:

- **Los umbrales de flatline y saturación se apagan.** Son propiedades del AFE
  DC-acoplado del chaleco; los registros públicos vienen AC-acoplados y ya
  centrados, y dejarlos prendidos mediría un artefacto del formato.
- **El bSQI no se aplica.** Necesita los R-peaks del firmware, que estos
  registros obviamente no traen. El motor lo detecta solo
  (`firmware_peaks_available` en falso) y no degrada el registro por una ausencia
  que no dice nada sobre la señal.

## Pausas del motor (`--pauses`, `--wander`, `--firmware-peaks`)

Mide las pausas que informa el motor contra los R-R anotados de MIT-BIH, y que el
ruido de NSTDB no invente ninguna. Es la evaluación de la regla de hueco quieto
(`app/ml/quiet_gap.py`): una asistolia larga deja ventanas enteras sin QRS, el
gate las rechaza y, sin esa regla, el motor no informaba pausa ni aviso al paciente.

```bash
cd back
uv run python -m tools.physionet.evaluate --pauses --jobs 8                # mitdb entero + nstdb
uv run python -m tools.physionet.evaluate --pauses --jobs 8 --wander 0.5   # + deriva respiratoria de 0,5 mV
uv run python -m tools.physionet.evaluate --pauses --jobs 8 --firmware-peaks DIR  # + los R del MCU
```

Evalúa los registros que haya en `data/mitdb` (o los de `--records`) y todos los de
`data/nstdb`. El `--download` de arriba solo trae los 7 de la Etapa 2: para los 48 de
`mitdb` y `nstdb` 118/119 hay que correr `--detectors --download`. Cada registro se
analiza entero, en un solo lote, sin refractariedad y sin intervalos; tarda segundos
con `--jobs 8`. Por registro y en el total (`Σ mitdb`, `Σ nstdb`):

| Columna | Qué cuenta |
|---|---|
| `anotadas` | R-R anotados de más de `ml_pause_seconds` (2,5 s) |
| `cubiertas` | de esas, las que alguna pausa del motor cubre de punta a punta (±0,15 s) |
| `pausas` | las que informó el motor |
| `hueco_quieto` | de las `pausas`, las que salieron por la regla de hueco quieto |
| `falsas` | pausas con un latido anotado adentro: cada una le avisaría al paciente una pausa que no existió |

En `nstdb` no hay R-R anotados de esa duración, así que toda pausa ahí es una que el
ruido inventó. Hoy: `mitdb` da 85 anotadas, 78 cubiertas, 80 pausas (10 por hueco
quieto) y 0 falsas; `nstdb`, 0 pausas.

Sin más, los flags van en cero: en producción el equipo **siempre** manda
`FLAG_R_PEAK`, y con flags en cero la mitad del árbol de decisión de la regla
—cotas confirmadas, veto del firmware, ventanas `marginal`, el censo de la
referencia— no se ejercita. `--firmware-peaks DIR` lee `DIR/<registro>.npy`: las
muestras (a 500 Hz) donde el detector del MCU confirma cada R, ~250 ms después del
pico, que es donde el equipo pone la marca. Se exportan con el arnés del repo hermano
(`EcgValidationHarness.h`: `EcgDetector` compilado en nativo, la señal de
`load_record` en µV como entrada). Con ellos: 85 anotadas, 82 cubiertas, 84 pausas
(14 por hueco quieto) y 0 falsas; `nstdb`, 0. Antes de exigirle forma y contraste a
una cota confirmada chica (`quiet_gap.ATTENUATED_SHAPE`), daban 3 falsas: los colapsos
de amplitud del 116 y el 208, que el firmware confirma al recuperarse.

`--wander <mV>` (solo con `--pauses`) le suma a `mitdb` una deriva respiratoria de
0,25 Hz y esa amplitud, en minutos alternados de 60 s, como un paciente que cambia de
postura (`nstdb` ya trae la suya). Es el adversario de la regla: sobre un QRS chico, la
deriva deja ventanas `bad` por kSQI o pSQI y la regla tiene que seguir viendo los
latidos. Lo que no puede aparecer son `falsas`: con 0,5 mV dan 0, pero las `cubiertas`
bajan a 39 de 85.

## Benchmark de detectores de R (`--detectors`)

`app/ml/rpeak_detection.py` tiene dos detectores de QRS que todavía no se unificaron:
`nk` (NeuroKit, el del motor) y `pt` (Pan-Tompkins, el de las métricas Holter y de
`/holter-metrics`). `--detectors` mide su Se y PPV latido a latido, cada uno como corre
en producción, contra las anotaciones de MIT-BIH (ANSI/AAMI EC57 y criterios más
estrictos), contra NSTDB por SNR y contra el detector del firmware en las capturas del
chaleco (`tools/vest/detectors.py`). Método, resultados y lectura para la unificación:
[`DETECTORS.md`](DETECTORS.md).

```bash
cd back
uv run python -m tools.physionet.evaluate --detectors --jobs 8   # mitdb + nstdb + chaleco
```

Los datos se bajan antes con `--detectors --download` (los 48 de `mitdb` y `nstdb`
118/119; baja y sale). `--part mitdb,nstdb,vest` y `--records` acotan la corrida, y
`--summarize-only` rehace el reporte desde `data/detectors_results.json`, donde la
corrida deja los conteos por registro. Las capturas del chaleco se leen de
`../Holter-ECG-System/capturas/` (`--captures-dir` para otra ruta).
`python -m tools.physionet.detectors` acepta lo mismo.

## Etapa 3: intervalos contra la QT Database (`--qtdb`)

La pregunta acá es otra: no si el motor encuentra los latidos, sino si los
**intervalos** que mide se parecen a los que marcó un cardiólogo. Es el gate de
la Fase 3 del plan: **|bias| ≤ 20 ms en QRS y ≤ 30 ms en QT, por latido y por
mediana de registro**, con las definiciones del plan (QRS = R_onset → R_offset,
QT = R_onset → T_offset).

```bash
cd back
uv run python -m tools.physionet.evaluate --qtdb --download        # 105 registros, ~70 MB
uv run python -m tools.physionet.evaluate --qtdb --jobs 9          # ~2 min con 9 procesos
uv run python -m tools.physionet.evaluate --qtdb --variants        # + las variantes exploradas
uv run python -m tools.physionet.evaluate --qtdb --methods prominence,production  # ~25 s: el módulo contra su delineador
uv run python -m tools.physionet.evaluate --qtdb --methods prominence,production \
    --production-thresholds reject_truncated_t=false               # el módulo con una guarda apagada
uv run python -m tools.physionet.evaluate --qtdb --summarize-only  # re-agrega sin re-delinear
```

`python -m tools.physionet.qtdb` acepta lo mismo sin importar el resto del motor.
Los resultados van a `data/qtdb_results/` (ignorado, como `data/`): `summary.md`
con todas las tablas, `verdict.csv`, `metrics.csv`, `robustness.csv`,
`verdict_mlii.csv`, `production_blocks.csv` y `production_parity.csv` (cuando se
corre `production`; si no, se borran los de una corrida anterior), y el detalle
por latido y por registro en `beats.csv` y `records.json`. El código está
partido en tres: `qtdb.py` (datos, réplica de producción, CLI),
`qtdb_delineation.py` (delineadores alineados por latido, guardas y el hueco
`production`) y `qtdb_report.py` (métricas e informe). Si `production` estaba
pedido y no se pudo importar o reventó en algún registro, la corrida escribe el
informe igual pero sale con código 3.

### Cómo se mide

- **Referencia.** El anotador `q1c` (primer cardiólogo, segunda pasada): 103 de
  los 105 registros tienen latidos utilizables, **3542 latidos** (QRS manual
  111 ± 35 ms, QT 424 ± 73 ms). El pico del QRS lleva la etiqueta del latido
  (`N`, `A`, `V`…), así que se toman todos los símbolos de latido: si solo se
  buscara `N`, la T de un latido `V` se le asignaría al anterior.
- **Réplica de producción.** 250 → 500 Hz con `resample_poly`, `clean_signal` y
  `detect_rpeaks` importados de `app.ml.rpeak_detection` (no copias; hoy sin la
  corrección de artefactos de NeuroKit), y bloques de 300 s centrados en el
  tramo anotado.
- **Dos fuentes de R.** `detected` es lo que ve producción. `reference` aplica la
  misma regla de `nk.ecg_peaks` (máximo local más prominente) dentro del QRS
  manual ± 20 ms, y rellena los huecos del detector con la otra derivación. Para
  `prominence` y `production` difieren hasta 5 ms y ningún veredicto cambia; en
  la derivación 1 `dwt` difiere 5,5 ms en el QT, que pasa con `reference` y no
  con `detected` (es su 3/4), y `peak` hasta 8 ms en la mediana de bloque.
- **Delineación alineada por latido.** La API pública `nk.ecg_delineate`
  *descarta* los valores ≤ 0 en vez de volverlos NaN y corre un lugar todos los
  latidos siguientes (17 corridas de `peak` en QTDB). El harness llama al
  delineador interno y los vuelve NaN; `app.ml.intervals` hace lo mismo.
- **Guardas de los métodos de stock.** QRS válido si R_onset ≤ R ≤ R_offset; QT
  válido si R_onset ≤ R < T_offset < R siguiente del tren. El filtro `plaus`
  agrega el QT en 200–650 ms: un latido perdido por el detector deja un QT > 1 s
  aunque T_offset caiga antes del R siguiente del tren (sele0203 y sel853 en la
  derivación 1). Un delineador que revienta (`dwt` con `IndexError` a frecuencia
  muy baja en sel49; `cwt` con dos R a < 90 ms o un R pegado al borde) pierde
  ese bloque y queda listado en las notas, sin tumbar la corrida.
- **Métricas.** Bias, SD, MAE y cobertura por latido; bias y MAE de la mediana
  por registro (≥ 10 latidos válidos); la mediana de **bloque** (todos los
  latidos del bloque, ≥ 30 válidos: lo que guardaría producción) menos la
  mediana manual; IC 95 % por bootstrap de **registros**, porque los latidos de
  un registro no son independientes ("pasa con IC" = los IC por latido y por
  registro enteros dentro del gate); r y pendiente entre registros del QT **y
  del QTc**, y cómo clasificarían `qrs_wide` / `qtc_long` con la mediana de
  bloque.

### Resultado (NeuroKit 0.2.13, definición del plan)

Rangos sobre las cuatro combinaciones (2 derivaciones × 2 fuentes de R), en ms:

| método | QRS cob. % | QRS bias | QRS bias reg. | QRS pasa | QT cob. % | QT bias | QT bias reg. | QT bloque | QT pasa | QT con IC |
|---|---|---|---|---|---|---|---|---|---|---|
| `dwt` | 73–77 | +74 a +86 | +77 a +85 | 0/4 | 73–77 | +22 a +33 | +14 a +26 | +17 a +26 | 3/4 | 0/4 |
| `cwt` | 32–38 | +18 a +23 | +19 a +25 | 2/4 | 47–55 | +60 a +88 | +59 a +93 | +75 a +83 | 0/4 | 0/4 |
| `peak` (Q→S, Q→T_off)¹ | 88–94 | +26 a +36 | +29 a +37 | 0/4 | 85–90 | +23 a +25 | +23 a +28 | +29 a +40 | 4/4 | 0/4 |
| `prominence` | 94–100 | −29 a −28 | −27 a −25 | 0/4 | 89–95 | −12 a −6 | −8 a −3 | −8 a −4 | 4/4 | **4/4** |
| `production`² | no se reporta | — | — | — | 54–64 | −4 a −2 | −2 a −1 | +1 | 4/4 | **4/4** |

¹ `peak` no da R_onset / R_offset: su única forma de medir un QRS es
Q_peak → S_peak. ² `app.ml.intervals` tal como se despliega (`prominence` más sus
guardas, `IntervalThresholds()` por defecto), con los mismos R, la señal limpia
para delinear, todo el bloque como GOOD y como `raw_for_amplitude` la convención
del módulo: pasaaltos de 0,5 Hz (la primera etapa de `nk.ecg_clean`) más el
notch de red de `quality.remove_mains`, los dos de fase cero y sin pasabajos. La
red es la de cada registro: 50 Hz en los `sele*` (European ST-T) y 60 Hz en el
resto (bases grabadas en Boston), como en `evaluate.py`. Se regenera en cada
corrida.

El QT de las dos filas que se usan, por combinación:

| método | deriv. | R | cob. % | bias | SD | bias reg. | MAE reg. | bloque | IC 95 % del bias |
|---|---|---|---|---|---|---|---|---|---|
| `prominence` | 0 | reference | 93,3 | −5,7 | 61,2 | −2,7 | 33,8 | −4,0 | [−16,2; 5,3] |
| `prominence` | 0 | detected | 93,1 | −5,5 | 61,7 | −2,8 | 33,9 | −3,9 | [−16,5; 5,5] |
| `prominence` | 1 | reference | 94,6 | −12,0 | 58,9 | −8,5 | 34,1 | −8,4 | [−20,8; −2,5] |
| `prominence` | 1 | detected | 89,2 | −6,9 | 65,0 | −5,3 | 32,1 | −5,5 | [−16,0; 2,2] |
| `production` | 0 | reference | 63,9 | −4,0 | 51,0 | −1,9 | 29,1 | +1,2 | [−13,2; 4,8] |
| `production` | 0 | detected | 63,6 | −3,7 | 51,2 | −1,3 | 28,8 | +1,2 | [−13,4; 5,5] |
| `production` | 1 | reference | 54,7 | −2,1 | 45,3 | −1,7 | 30,0 | +1,3 | [−11,2; 6,5] |
| `production` | 1 | detected | 54,4 | −2,1 | 45,1 | −2,2 | 30,0 | +1,3 | [−11,4; 6,8] |

### El módulo desplegado (`production`) contra su delineador

`production` llama a `app.ml.intervals.measure_beats` / `measure_intervals` y
se mide con la misma vara que los métodos de stock. Usa el mismo delineador, así
que tiene que dar lo mismo que `prominence` salvo por sus guardas propias, y lo
da:

- **Por latido, el QT es idéntico** (diferencia máxima 0,00 ms en los 1928 a
  2265 latidos por combinación que los dos aceptan) y el QTc también.
- **Todo latido que `production` acepta lo acepta `prominence` con el filtro
  `plaus`** (0 al revés), y **cada latido que `plaus` acepta y `production` no
  se explica por una guarda propia del módulo**, leída de las máscaras que el
  propio módulo expone (`BeatIntervals`): ninguno queda sin explicar. Por
  combinación salen 265 a 385 por RR, 1 a 128 por R fuera del pico, 362 a 430
  por T cortada y 259 a 501 por QRS negativo.
- Con las guardas nuevas abiertas (`--production-thresholds` con
  `rr_min_ratio=0`, `max_heart_rate_bpm=200`, `peak_tolerance_ms=1000`,
  `reject_truncated_t=false`, `reject_negative_qrs=false`,
  `min_coverage_ratio=0`) reproduce exactamente los números del módulo anterior.

**Las guardas.** Cada una es geometría del delineador o práctica clínica y se
fijó sin mirar el error contra QTDB (el detalle está en `IntervalThresholds`).
En QTDB cada una saca latidos peor medidos que los que deja: los que saca por RR
tienen MAE de 48 a 61 ms, los de QRS negativo de 41 a 46 y los de T cortada de 36
a 47, contra 32 a 34 de los que quedan; los de R fuera del pico (casi todos con
R de referencia en la derivación 1, donde la regla de `nk.ecg_peaks` no cae en el
máximo) tienen bias de −37 ms. Lo que cuesta cada una, con R detectados (L0 /
L1):

| configuración | bloques | QT bloque, bias (MAE) | QTc ≥ 470: bias de bloque (salen ≥ 470 / con bloque) |
|---|---|---|---|
| todas (default) | 64 / 59 | +1,2 (23,6) / +1,3 (28,2) | −26 (4/8) / −25 (5/10) |
| sin T cortada | 75 / 66 | +1,8 (27,8) / +0,4 (28,7) | −32 (5/12) / −30 (4/11) |
| sin QRS negativo | 72 / 74 | +0,1 (25,3) / +3,9 (27,6) | −25 (4/9) / −30 (5/11) |
| sin cobertura mínima | 80 / 70 | −2,8 (30,6) / −1,3 (29,5) | −60 (6/15) / −30 (5/12) |
| sin piso de prematuridad | 65 / 60 | +0,4 (23,9) / 0,0 (28,9) | −29 (5/9) / −30 (5/11) |
| módulo anterior (sin ninguna de las nuevas) | 101 / 99 | −5,5 (32,6) / −6,4 (33,1) | −38 (8/20) / −40 (7/18) |

El techo de FC y la guarda de R fuera del pico no cambian nada en QTDB (casi no
hay tramos de más de 100 lpm, la T cortada ya los saca, y los R detectados caen
en el pico); están por el chaleco y por los casos sintéticos de
`test_ml_intervals`. **El precio es la cobertura**: el bloque sale en 64 y 59 de
los 103 registros en vez de 101 y 99, y el 54-64 % de los latidos anotados
queda válido. Para un dato de investigación es el lado correcto, con una
salvedad que el consumidor tiene que respetar: **un bloque `None` no es un QT
normal**. Las guardas sacan justo los registros de QT largo (con bloque quedan 8
y 10 de los 18 a 21).

Lo que guardaría producción por bloque de 300 s (`IntervalMeasurement`):

| deriv. | R | bloques con medición | `coverage_ratio` media / mediana / p10 | QT bloque, bias (MAE) | QTc bloque, bias (MAE) |
|---|---|---|---|---|---|
| 0 | reference | 64/103 (62,1 %) | 0,909 / 0,954 / 0,755 | +1,2 (23,6) | +0,6 (23,7) |
| 0 | detected | 64/103 (62,1 %) | 0,908 / 0,954 / 0,755 | +1,2 (23,6) | +0,6 (23,7) |
| 1 | reference | 59/103 (57,3 %) | 0,875 / 0,944 / 0,623 | +1,3 (28,2) | +0,1 (28,3) |
| 1 | detected | 59/103 (57,3 %) | 0,875 / 0,944 / 0,627 | +1,3 (28,2) | +0,2 (28,3) |

La amplitud R, contra la referencia manual (señal cruda remuestreada en R_ref
menos el onset manual):

| deriv. | R | est/ref por latido (mediana) | est/ref por bloque (mediana) | bias por latido, mV | `prominence` sobre la señal limpia, est/ref |
|---|---|---|---|---|---|
| 0 | reference | 1,008 | 1,027 | +0,030 | 0,804 |
| 0 | detected | 1,008 | 1,027 | +0,041 | 0,804 |
| 1 | reference | 1,020 | 1,024 | +0,044 | 0,821 |
| 1 | detected | 1,019 | 1,024 | +0,044 | 0,818 |

**El techo de QT plausible (`qt_max_ms` = 650 ms) es un compromiso.** QTDB
tiene QT reales por encima: sel33 y sele0116, bradicárdicos (RR de 1,6 a
1,7 s), con QT manual de 764 y 726 ms, que con el techo quedan sin bloque.
Subido a 800 ms (`--production-thresholds qt_max_ms=800`) entran 3 y 1 bloques
más: sel33 da 668 / 664 ms (L0 / L1) y sele0116 658 ms en L0 (cortos, por lo de
la sección siguiente), pero también sel14172 L0 con 680 ms contra 410 del manual
(`prominence` pone el fin de T lejos), y el MAE de bloque pasa de 23,6 / 28,2 a
29,0 / 29,3 ms. Queda en 650 ms.

### Lo que el módulo no puede medir: el QT largo

Que el bias pase no dice que la medición siga al paciente, y en QT largo no lo
sigue:

- **El T_offset queda a ≤ 100 ms del pico de la T.** `prominence` lo busca con
  `peak_prominences(wlen = max_t_basepoint_interval = 200 ms)`: en el 57-62 % de
  los latidos válidos queda exactamente en T_peak + 100 ms. Con una T normal
  alcanza (pico → fin de T manual: mediana 92 ms), pero una T ancha termina más
  lejos (132-136 ms con QTc manual > 500 ms). La fracción topeada no avisa: es
  casi la misma con QT normal y largo (0,54-0,60 contra 0,56-0,66 por registro)
  y no correlaciona con el error (r ≈ 0,1).
- **El QTc casi no sigue al manual entre registros.** El QT de `prominence`
  correlaciona r = 0,71-0,80, pero el QT manual contra el RR ya da 0,68-0,69: es
  frecuencia cardíaca. El QTc de `production` da r = 0,24-0,30 por registro
  (pendiente 0,17-0,20) y 0,27-0,50 con la mediana de bloque (pendiente
  0,25-0,34), con un p90 del error absoluto por registro de 56 a 71 ms. El de
  `prominence` sin guardas, 0,39-0,58: las guardas no miden peor, sacan los
  registros de QT largo, que son los que dan rango.
- **QTc ≥ 470 ms.** Con la mediana de bloque, `production` sale 25-26 ms corto
  y marca 4-5 de los 8-10 registros que reportan; `prominence`, 40-71 ms corto y
  7-9 de 18-21.

Sobre ECG sintético (`test_ml_intervals`): un QTc de 570 ms a 60 lpm sale 518
con todos los latidos válidos; a 80 lpm, la T larga ya no entra en el segmento
R + RR/2 (se veía 451 con QTc real 500); a 90 lpm el pico de la T cae fuera y
`prominence` da un QT de ~240 ms. Las guardas convierten los dos últimos en
`None`; el primero no lo detecta nada.

### Decisión

- **Ningún método de stock pasa el gate completo** con la definición del plan.
  Como prevé el plan para ese caso, las mediciones salen **experimentales** y
  **no hay hallazgos de intervalos**: `qrs_wide`, `qtc_long` y `qtc_short` no
  existen en el motor, ni detrás de un flag. Ver "En producción: dato de
  investigación" más abajo.
- **QT y QTc con `prominence`.** Es la única medición con evidencia a nivel
  gate: pasa el QT en las cuatro combinaciones, con los dos fiduciales, con IC y
  con mediana de bloque, y tarda ~0,03 s por bloque.
- **El QTc es un dato de investigación, no un número por paciente.** No se le
  muestra al médico como valor del paciente: subregistra el QT largo con
  cobertura completa (sección anterior) y un bloque sin medición no es un QT
  normal. Mostrarlo exige un delineador que pueda ver una T ancha o tardía y
  capturas del chaleco anotadas.
- **El ancho de QRS no se reporta, ni siquiera como experimental.** `prominence`
  lo topea en 100 ms por construcción: `peak_prominences(wlen =
  max_r_basepoint_interval = 100 ms)` deja R_onset y R_offset a ≤ 50 ms del R.
  El máximo estimado es exactamente 100,0 ms y ninguno de los latidos con QRS
  manual ≥ 120 ms sale ≥ 120 (`qrs_wide` con la mediana de bloque: 0 VP y 28-29
  FN sobre 103 registros); mostrarlo haría parecer normal cualquier QRS. Las
  alternativas tampoco sirven: `dwt` da +74 a +86 ms y `cwt` cubre el 32–38 %.
- **Amplitud R.** La limpieza de producción (una media móvil de 10 muestras ida
  y vuelta, un pasabajos de ~16 Hz) la atenúa ~20 %: mediana estimada/referencia
  0,80–0,82. Por eso `measure_intervals` la mide sobre `raw_for_amplitude` y no
  sobre la señal limpia, en el pico al que `prominence` corre el R: con la
  convención del módulo (pasaaltos + notch de red) da 1,01–1,02 por latido y
  1,02–1,03 por bloque.

### En producción: dato de investigación

Con `ML_INTERVAL_MEASUREMENTS_ENABLED` (prendido por omisión) el motor mide
cada bloque que analiza (`pipeline.analyze_batch` → `_measure_intervals`) y
guarda una fila por bloque en `ecg_interval_measurement`. **Nada del producto
la lee**: ni una API, ni el informe, ni el visor, ni un hallazgo;
`tests/test_ml_interval_rows.py` verifica que ningún schema de OpenAPI tenga un
campo de intervalos. Es para juntar mediciones del chaleco real y compararlas
con lo validado acá.

- **Qué se mide.** `measure_intervals` tal cual lo validó `production`, con
  los umbrales por defecto de `IntervalThresholds`, sobre:
  - `cleaned`: la señal de `clean_signal` del bloque, la misma que delinea
    QTDB;
  - `raw_for_amplitude`: el bloque con el riel del AFE puenteado, la red
    quitada (`quality.remove_mains` a `ML_MAINS_HZ`) y el pasaaltos de 0,5 Hz de
    orden 5 de fase cero del harness (`highpass`), sin pasabajos;
  - el tren de `detect_rpeaks` del bloque entero;
  - las ventanas GOOD del bloque, sin el entorno de los empalmes;
  - la morfología dominante de la asignación del bloque.

  Se miden solo los latidos con el R en la parte nueva (`owned`): el contexto
  izquierdo aporta el R-R previo del primero y el derecho deja terminar la T
  del último, pero cada latido entra en la mediana de un solo bloque. Dos
  bloques consecutivos suman los mismos candidatos que el registro de una vez.
- **Una fila por bloque medido.** Clave `(study_id, start_sample_index)` con el
  inicio de la parte nueva, idempotente. Un bloque sin medición (menos de
  `min_beats` latidos válidos, cobertura < 0,5, bigeminismo) **no deja fila**: el
  denominador, los bloques analizados, está en `signal_quality_interval`.
  `qrs_ms` va siempre en NULL y `experimental` siempre en verdadero.
- **Nunca tumba el bloque.** Un error de la medición se registra
  (`ml_interval_measurement_failed`) y el bloque sigue sin fila: si la pasada
  del motor fallara, su cursor no avanzaría y `ML_ANALYSIS_PENDING` trabaría el
  informe del estudio.
- **Costo, medido** (bloque de producción: 60 s de contexto + 300 s + 30 s de
  contexto derecho, ECG sintético, Apple M4, solo CPU): el análisis entero
  pasa de 0,034 a 0,063 s, **+0,029 s por bloque** a 70 lpm (+0,032 s con un
  ectópico cada cinco latidos; +0,009 s con bigeminismo, que no llega a
  delinear porque la máscara dominante deja menos de `min_beats` candidatos).
  Un bloque sin latidos sobre señal GOOD no filtra ni delinea nada.

Para exportarlas:

    uv run python -m app.scripts.export_interval_measurements --out intervalos.csv
    uv run python -m app.scripts.export_interval_measurements --study <uuid>   # a la salida estándar

Una fila por bloque, en orden de registro: `study_id`, la posición del bloque
(`start_sample_index`, `sample_count`, `sample_rate`, y en segundos `start_s` y
`duration_s`, que son **tiempo de registro**, no hora de pared), `beats`,
`candidate_beats`, `coverage_ratio`, `qt_ms`, `qtc_ms`, `r_amplitude_mv`,
`heart_rate_bpm`, `candidate_heart_rate_bpm`, `qrs_ms` (vacío), `method`,
`experimental`, `model_version` y `batch_id`. **Ningún dato del paciente ni
ninguna fecha**: el estudio es la única referencia, y la hora en que se escribió
la fila (`created_at`) queda afuera porque fecharía el monitoreo de cada
paciente. Los estudios de pacientes dados de baja no se exportan.

Lo que sirve para la tesis: la distribución del QTc y de la cobertura en el
chaleco contra la de QTDB, cómo cambian con la colocación (seco, gel, bien
puesto) y con la frecuencia, y la amplitud R por colocación. Lo que no: un QTc
por paciente, por las mismas razones de arriba. Un bloque sin fila no es un QT
normal, y un QT largo sale corto con cobertura completa.

### Lo que se exploró y quedó afuera de la corrida por defecto

- **Con `--variants`:** `prominence~rb200` (`max_r_basepoint_interval = 200 ms`,
  un kwarg público) y la definición Q_peak → S_peak / Q_peak → T_offset para
  todos los métodos. Las dos variantes de `prominence` pasan el gate en las
  cuatro combinaciones (QRS +4 a +8 y +1 a +4 ms; QT +9 a +14 y +10 a +15 ms,
  con IC), pero porque los dos bordes caen temprano y se cancelan. No siguen el
  ancho entre registros (r ≤ 0,34; `qrs_wide` 16–18 VP con 21–30 FP) y en MLII,
  la derivación más parecida al chaleco, fallan el QRS (+31 / +38 y +20 / +24 ms
  por mediana de registro). Se encontraron mirando QTDB: **requieren
  justificación** y validación con capturas del chaleco antes de mostrar un
  ancho de QRS. En NeuroKit 0.2.13, `dwt` y `cwt` dan las mismas Q y S que
  `peak`. (Medido con la detección anterior, con corrección de artefactos.)
- **Sacado del harness** (medido una vez sobre los mismos datos, con las mismas
  versiones):
  - `+biosppy`, delinear sobre `nk.ecg_clean(method="biosppy")` (FIR de 0,67 a
    45 Hz, sin el pasabajos de ~16 Hz): ninguna combinación pasa con la
    definición del plan; `prominence` empeora (QRS −39 a −41 ms, QT −22 a
    −27 ms).
  - `@250`, delinear `cwt` a 250 Hz, la tasa para la que están fijadas sus
    escalas (Martínez et al. 2004): cobertura del 12–16 % y QT de +87 a +91 ms.
  - Otros fiduciales del R de referencia: argmax|·| en ± 30 ms de la marca caía
    en S/QS en el 22 % / 53 % de los latidos (L0 / L1) y coincidía ± 4 ms con el
    detector en el 75 % / 48 %; el máximo local positivo con fallback, en el
    85 % / 66 %. La regla de `nk.ecg_peaks` dentro del QRS manual ± 20 ms
    coincide en el 97 % / 96 %, y el margen se eligió por ese acuerdo, nunca por
    el error contra el gate.
  - Corrección de bias constante estimada con validación cruzada por registro:
    `prominence` con un offset de QRS de ≈ −21 ms "pasa", pero el techo queda en
    ~120 ms y sigue sin poder marcar un QRS ancho.
  - Selección del método con validación cruzada por registro (5 folds ×
    derivación × fuente de R): alguna de las dos variantes gana en los 20 folds
    y pasa en 17 de 20 de prueba.
  - Subconjuntos de polaridad positiva y de latidos comunes a todos los
    métodos: no cambian el titular de los métodos de stock.

### Reproducibilidad

El harness es el port del benchmark exploratorio con el que se tomó la
decisión. Con las mismas versiones y la detección de entonces (con corrección
de artefactos) reproducía sus números **exactamente**: las 70 840 filas por
latido de los métodos de stock y todas las estimaciones puntuales del veredicto,
con diferencia 0,0 ms. Las versiones se leen de los metadatos del paquete
instalado (`importlib.metadata`) y quedan en `metrics.json` y en `summary.md`:
NeuroKit 0.2.13, NumPy 2.4.6, SciPy 1.18.1, pandas 2.3.3, PyWavelets **1.9.0**
(el wheel todavía dice `pywt.__version__ == "1.8.0"`) y wfdb 4.3.1. `beats.csv`
sale ordenado por registro, derivación, fuente de R, método y latido, así que
dos corridas iguales dan archivos iguales bit a bit. Los IC por bootstrap usan
un generador por configuración: el de un método no depende de qué otros se
corrieron.

### Qué no se puede concluir

Lo mismo que con MIT-BIH: QTDB son registros de Holter con **electrodos de
gel**, en derivaciones variadas (MLII solo en 12 registros), y los latidos anotados
los eligió el anotador, así que son latidos limpios. Alcanza para
elegir el delineador y descartar los que no sirven; no para afirmar que el QT
del chaleco tiene este bias.
