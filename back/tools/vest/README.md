# Evaluación contra capturas reales del chaleco

Corre el motor de detección (`app/ml`) sobre las capturas de telemetría que el
equipo de Biomédica grabó con el chaleco, y compara el gate de calidad contra lo
que se sabe de cada captura. Es la contraparte de `tools/physionet/`: PhysioNet
sirve para calibrar con electrodos de gel colocados por un técnico; esto mide el
hardware real, con electrodos secos y la red eléctrica de una casa.

## Datos

Las capturas **no viven en este repo**: están en el repo hermano
`../Holter-ECG-System/capturas/` (al lado del monorepo). Se usan solo las de
**canal 2** (`captura_canal2_*.txt`), que es el ECG; el canal 1 es respiración y
evaluar el gate ahí mide otra cosa.

Cada archivo transmite un solo canal: la columna `raw_ch0` es la señal en mV, con
encabezados `#META`/`#FIELDS` repetidos e intercalados con líneas de log. Los
flags que recibe el motor se arman con las columnas del firmware:

| Columna | Flag |
|---|---|
| `r_lag_ms > 0` | `FLAG_R_PEAK` (la confirmación del latido; el motor compensa el retardo) |
| `lead_off` o `leadoff_susp` | `FLAG_LEAD_OFF` (comparador del AFE o detector por señal) |
| `\|raw_ch0\| ≥ 383,2 mV` | `FLAG_ADC_SATURATED` (95 % del fondo de escala, como el firmware) |
| `rld_off` | `FLAG_RLD_OFF` |
| `sqi_level` | los bits de SQI del firmware |

- Las 16 capturas traen `leadoff_susp`, pero el firmware recién lo marca en la
  trama desde el 30/9 (`LEADOFF_SIGNAL_MARCA_TRAMA`): las anteriores se evalúan
  como si las hubiera grabado el firmware de hoy.
- La captura no trae el bit de saturación: se rehace con el umbral del firmware
  (`ADC_SATURATION_CODE_THRESHOLD`, 95 % de VREF/ganancia = 2,42 V / 6). El
  `#REGS` de las 16 capturas confirma VREF de 2,42 V y ganancia 6 en CH2SET.
- Muestras perdidas: el contador `n` se lee con las reglas de `tools/banco.py`
  del repo hermano (un `n` que no avanza o que salta más de 300 s es otra
  referencia; en un salto real también se descarta la fila del salto). Los
  huecos **se cierran**, no se reinsertan: el tiempo posterior a un salto queda
  corrido. Hoy solo `ab_cargador_vecino` tiene uno (49 muestras, ~0,1 s).

## Uso

```bash
cd back
uv run python -m tools.vest.evaluate                                  # todas las de canal 2
uv run python -m tools.vest.evaluate captura_canal2_aviso_ll_ra --timeline
uv run python -m tools.vest.evaluate --mains-hz 0                     # sin quitar la red
uv run python -m tools.vest.evaluate --captures-dir /otra/carpeta
```

Cada captura se analiza como **un solo lote** con un banco de plantillas nuevo:
`build_config(settings, 500)` + `analyze_batch`, las mismas funciones que usa la
ingesta. La ingesta, en cambio, analiza lote por lote, del largo que mande el
chaleco: los bordes de lote caen en otro lado, y eso solo toca el primer y el
último segundo de cada lote (donde la curtosis no mira la señal sin red). Por
captura imprime ventanas `good`/`marginal`/`bad`, conteo por motivo, medianas de
pSQI/kSQI/basSQI/bSQI, cuántas ventanas falla cada índice por separado y los
hallazgos. `--timeline` agrega una línea por ventana de 10 s. Tarda ~10 s con
las 16 capturas.

## Expectativas

Al final compara contra lo fijado en el plan (Verificación, paso 4) y contra las
guardas de regresión, y sale con código 1 si alguna falla:

| Captura | Esperado |
|---|---|
| `captura_canal2_seco_limpia` | ≥ 28/35 `good` |
| `captura_canal2_seco_ajustado` | ≥ 26/29 `good` |
| `captura_canal2_ab_tapa_router` | 0 `good` |
| `captura_canal2_movimiento_con_puente` | 0 `good` |
| `captura_canal2_aviso_ll_ra` | 0 `good` entre 30-95 s y 238-295 s |
| `captura_canal2_loff0C_seco_saturada` | todas `lead_off` |

En `aviso_ll_ra` los dos tramos son las dos perturbaciones de la captura y el
criterio es estricto: cuenta toda ventana que **toque** el tramo, aunque sea un
instante.

**Las expectativas no se ajustan a los resultados.** Si una falla se investiga
el motor y se informa; mover el número para que pase deja a la herramienta sin
medir nada. Los umbrales de los SQIs tampoco se calibran contra estas capturas:
son pocas y del mismo chaleco, alcanzan para detectar una regresión, no para
validar.

Lo único del motor que se miró contra ellas es el Q del notch de red, y solo
como verificación: el valor sale de la respuesta en frecuencia (ver
`MAINS_NOTCH_Q` en `app/ml/quality.py`). De Q = 15 a Q = 30 las 16 capturas dan
ventanas idénticas; con Q = 60 `seco_ajustado` cae a 25/29. Esa expectativa
queda **justo en el límite** (26/29), así que cualquier cambio que la mueva hay
que mirarlo ventana por ventana.

### Guardas de regresión

No salen del plan sino de lo que el motor da hoy, revisado ventana por ventana
contra la bitácora de cada captura. Fijan las dos direcciones en que un cambio
de Q o de umbral puede romper algo sin que ninguna expectativa lo note:

| Captura | Guarda |
|---|---|
| `captura_canal2_leadoff_head_con_puente` | ≤ 1 `good` (160-170 s, en el límite: 12-15 mV de red del router) |
| `captura_canal2_leadoff_final` | ≤ 3 `good` |
| `captura_canal2_leadoff_piel_cargador` | ≤ 12 `good` |
| `captura_canal2_ab_router` | todas `good` en 130-210 s y 380-450 s (posiciones "cerca": 2-4 mV de red, sin ráfagas) |

## Lo que mostró

- Sin quitar la red (`--mains-hz 0`), `seco_ajustado` da **0/29**: ~2 mV pico a
  pico de 50 Hz sobre un ECG perfectamente visible bajan la curtosis a ~2 y el
  kSQI rechaza todo. Quitando la red (notch en la fundamental y las armónicas,
  solo para los índices espectrales) da **26/29**, sin abrir ninguna de las
  ruidosas (`ab_tapa_router`, `movimiento_con_puente`, `leadoff_final`,
  `leadoff_piel_cargador`).
- Abre ventanas en otras cinco, sin expectativa del plan: `ab_router` 16 → 31,
  `aviso_ll_ra` 12 → 20, `ab_cargador_vecino` 6 → 10, `leadoff_broches` 2 → 5 y
  `leadoff_head_con_puente` 0 → 1. Contra la bitácora, las cuatro primeras son
  tramos con los electrodos bien y solo red encima; la última es la que quedó
  en el límite (ver las guardas).
- La red aparece entre 50,14 y 50,34 Hz según la captura, y las armónicas en
  múltiplos de eso: es el reloj de muestreo del chaleco. Por eso el notch usa Q
  constante, y bajo (15): a esos corrimientos un notch angosto deja pasar
  bastante red.
