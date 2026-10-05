"""Pausas por hueco quieto: la asistolia que el gate de calidad no deja ver.

Una pausa es un R-R largo, y `arrhythmia.detect_rhythm` solo mira los R-R
**válidos**: los que caen enteros en ventanas `good`. Eso falla justo en la
pausa que más importa. Cuando una asistolia dura lo suficiente para que una
ventana de 10 s entera quede sin un QRS, la ventana sale `bad` —casi siempre
por pSQI, kSQI o basSQI, que corren antes que `no_beats`; con ruido nulo, por
`flatline`—, el R-R que la cruza queda inválido y el motor no informaba nada:
ni pausa ni aviso al paciente. Medido sobre 1584 asistolias sintéticas de 3 a
60 s: desde 10 s ninguna salía completa y desde 20 s no salía ninguna pausa.

Abrir el gate no es la solución: existe para que el ruido de un electrodo seco
no se convierta en arritmias, y una ventana con ruido de movimiento no tiene
QRS igual que una asistolia. Lo que separa las dos cosas —medido, no supuesto—
es la amplitud del interior del hueco **relativa a los latidos del propio
paciente**: en una asistolia lo que queda entre los dos R es la línea de base
(y, en un bloqueo AV, las P), a menos de 0,22 veces el QRS; en el ruido del
chaleco que no vetó el hardware, 0,40 o más, y en el de NSTDB, 0,91 o más. Ni
la amplitud absoluta, ni la curtosis, ni la cantidad de picos de NeuroKit
separan nada.

**Qué R son latidos.** La referencia es la amplitud pico a pico (±60 ms sobre
la señal limpia) de los latidos del paciente a ±`REFERENCE_SPAN_S`: los R de
NeuroKit en ventanas `good`, más los que el firmware confirmó en ventanas
`marginal`/bSQI —en un bloqueo AV completo NeuroKit marca cada P y el bSQI deja
todas las ventanas `marginal`, pero los QRS que los dos detectores vieron son
latidos— o `bad` solo por basSQI (la deriva respiratoria; todo se mide sobre la
señal limpia, que ya no la tiene). Se toma la **población dominante**: la
mediana, sin los que miden menos de `REFERENCE_FLOOR` de ella (P tomadas por R).
Con el percentil 90 de antes, un 10 % de extrasístoles grandes se volvía "el
latido del paciente" y los normales quedaban como hueco quieto. Y tiene que ser
**el ritmo del paciente**: al menos `CENSUS_MIN` de los R de NeuroKit que miden
`CENSUS_FLOOR` o más de ella (si el firmware confirma solo las extrasístoles, la
referencia eran las V), sin contar los que no tienen T —las P de un bloqueo AV
completo, `CENSUS_T_MIN`—, y, con el firmware arbitrando, al menos
`MIN_REFERENCE_BEATS` confirmados (en una asistolia de dos minutos la referencia
local de un R falso eran otros R falsos). Con eso, un R:

- **acota** una pausa si mide entre `BOUND_MIN` y `BOUND_MAX` de la referencia;
  si el firmware también lo vio (`tolerance_samples`) y mide al menos
  `GOOD_BEAT_MIN`, sin tope; o si abre o cierra un **tren de escape**
  (`_train`: `TRAIN_BEATS` R parejos a 50 lpm o menos, más grandes que la P
  del paciente, que sobresalen de lo que hay entre ellos), que el detector del
  MCU no ve si es ancho y chico. Una cota por debajo de `BOUND_MIN` tiene que
  ser un escape y no un latido del paciente atenuado: sobresalir de lo quieto
  que cierra (`QUIET_MAX_UNDER_WEAK_BOUND`) y, si es de transición —la siguen
  latidos en ritmo que vuelven a crecer, `_Block.transitional`—, no tener su
  forma (`ATTENUATED_SHAPE`). Es el colapso de amplitud que se recupera, que el
  firmware confirma con el umbral ya bajo (MIT-BIH 116 y 208 con sus flags
  reales); un pop suelto que el firmware confirma o un nivel nuevo sostenido
  (los latidos que vuelven atenuados después del síncope) no lo son;
- **corta** el hueco si lo acota, o si es de una ventana `good` donde el
  firmware no arbitró (bloque sin flags) y mide al menos `GOOD_BEAT_MIN`. Donde
  el firmware sí arbitró, un R de NeuroKit que no es creíble ni confirmado no
  corta: las P de un bloqueo AV paroxístico miden hasta 0,15-0,39 del QRS
  (chaleco, MIT-BIH) y partían la asistolia en pedazos que no se informaban.

La regla (`_quiet_gap_pauses`) informa una pausa entre dos R consecutivos de los
que cortan, los dos cotas contra la referencia **del hueco** —la de
`[R1 − 60 s, R1] ∪ [R2, R2 + 60 s]`, no la del centro, que en una asistolia de
dos minutos es el hueco mismo—, cuando:

- **El interior está quieto.** `[R1 + 0,5 s, R2 − 0,3 s]` no tiene nada por
  encima de `QUIET_MAX` veces la referencia (máximo pico a pico local de
  120 ms), ni por encima de `QUIET_BAND_MAX` en la banda del QRS. Las P del
  paciente no cuentan aunque lo pasen (`P_WAVE_MARGIN`: un bloqueo AV con P
  grandes). Hasta `MAX_EVENTS` transitorios cortos (`EVENT_MAX_S`) que
  sobresalen de lo quieto (`EVENT_CONTRAST`) —un escape que ninguno de los dos
  detectores vio, un pop de electrodo— no anulan el hueco: si tienen la
  pendiente de un latido (`EVENT_BEAT_QRS`) y no son la T del R que abre
  (`T_ZONE_S`, `_Block.own_t_wave`) lo parten y se informa cada tramo quieto de
  más de `pause_seconds`; si no, quedan adentro del tramo, fuera de lo que se
  mide. Que en esos tramos no hubo latidos es cierto sea lo que sea el
  transitorio. Entre el R que abre y el interior no se mide lo quieto, pero una
  extrasístole temprana (`EARLY_FROM_S`, `_Block.early_beat`) es el latido
  desde el que corre la pausa, y si no es una cota creíble, es una chica.
- **El contacto no cambió.** La red (lo que `deinterfere` le quitó a la
  señal) y la deriva lenta del interior no superan `MAINS_MAX` y `DRIFT_MAX`
  veces las de las ventanas `good`/`marginal` vecinas. Es lo que separa una
  asistolia de latidos atenuados por pérdida de contacto: en `aviso_ll_ra`
  (43,2-47,3 s) hay 4 s quietos sin R del firmware, con la red 19,6 veces y la
  deriva 7,4 veces más altas que alrededor.
- **El firmware no vio latidos ahí**, si el bloque trae sus `FLAG_R_PEAK`.
- **No falta señal** (`_Block.missing`). Ninguna muestra `LEAD_OFF`,
  `ADC_SATURATED` o no finita, ninguna ventana `lead_off`/`saturated` y ningún
  empalme (`frame_gap`, `internal_gap`, pérdidas) entre los dos R, ni una
  muestra que el firmware marcó `SQ_BAD` (en una ventana `firmware_sqi`), un
  **riel** (`rail_mask`: un segundo de señal cruda casi constante, aunque no
  llene una ventana) ni una ventana `flatline` en el interior. Eso es señal
  que no existe, no latidos que no existieron. El borde de una corrida de la
  línea de tiempo no hace falta mirarlo: un bloque nunca la cruza
  (`processing._pending_blocks`).

**Tramos abiertos.** Donde falta uno de los dos R, lo que se sabe es que hasta
ahí, o desde ahí, no hubo latidos, y la pausa se informa por lo menos así:

- `openStart`: con el cursor de 300 s, 60 de contexto y 30 de contexto
  derecho, una asistolia de más de 90 s puede no tener nunca sus dos R en la
  lectura de un mismo bloque. El que lee el que la cierra, si su lectura
  empieza con contexto y el R es de su parte nueva, la informa desde el
  principio de su lectura.
- `openEnd`: un tramo que llega quieto hasta el final de la lectura, desde
  `OPEN_END_MIN_S`. Es la asistolia en curso o la que sigue hasta el final de
  la corrida: el primer bloque que la ve avisa ya, y el que lee el R que la
  cierra la completa.
- Los bordes de una ráfaga de ruido (`_Block.stretches`): un hueco con un
  artefacto de segundos no se puede afirmar entero, pero lo quieto entre el R y
  la ráfaga sí, desde `NOISE_EDGE_MIN_S`. A los 8-10 s de asistolia el paciente
  se desmaya, cae o convulsiona, y el artefacto anulaba la pausa entera.

Lo que dos bloques informan de una misma asistolia se solapa, y la persistencia
lo empalma en un evento con un solo aviso (`episodes.open_edges` resuelve qué
lado quedó abierto).

La misma medida depura las pausas que el motor ya daba con un R-R válido
(`refine_pauses`): si el interior tiene algo del tamaño de un latido
(`ENGINE_VETO`) **y con la pendiente de un QRS** (`ENGINE_VETO_QRS`, en la
banda `QRS_BAND_HZ`), no es una pausa sino un latido que NeuroKit no vio. En
MIT-BIH eran 46 pausas falsas —41 en el 207— que avisaban al paciente; las 72
verdaderas quedan (interior ≤ 0,16 contra 1,14-2,37 de las falsas; en banda
QRS ≤ 0,10 contra 0,80-1,38). La banda es lo que deja pasar la pausa
post-extrasistólica: la T tardía y grande de la extrasístole que la abre cae
en el interior con 0,7 del QRS de amplitud, pero es lenta. Si el R-R cruza un
riel, mide el riel y no el corazón: también se descarta. Y si tiene una
extrasístole temprana, la pausa corre desde ella (o deja de serlo).

**Lo que no resuelve.**

- Un colapso de amplitud a 0,2× o menos sin cambio de contacto, sin que el
  firmware vea esos latidos y con cotas creíbles, o una caída de señal del
  AFE, no se distinguen de una asistolia con ningún rasgo medido: por
  amplitud, por pendiente y por forma, un QRS a 0,2× es una P de un bloqueo AV
  (en MIT-BIH, la correlación con la plantilla da 0,76 para unos y 0,77 para
  otras). Entre avisar una asistolia real y no inventar una sobre latidos
  colapsados que nadie vio, la regla elige lo primero. Un tramo abierto no
  tiene la cota que delata la recuperación de un colapso: por eso se les pide
  más duración. Lo mismo un colapso que termina en un nivel nuevo sostenido:
  la guarda de forma mira solo las transiciones que se recuperan. Y un tramo
  abierto (`openEnd`, borde de una ráfaga) sobre latidos que colapsan de golpe
  a 0,2× o menos (0,3× junto a una ráfaga de movimiento) sale CRITICAL: el
  firmware queda ciego ~10 s después de un artefacto real.
- QRS normales de 0,2× o menos de las extrasístoles, si el firmware confirma
  solo estas: miden como una P y el censo los deja afuera; los intervalos V-V
  pueden salir como pausas (sintético: normales de 0,17-0,22 mV contra V de
  1,5 mV). Y una extrasístole de 0,2× o menos que nadie confirma, o una muy
  ancha (QRS de ~200 ms) de 0,35×, que en banda QRS mide menos que
  `EVENT_BEAT_QRS`, con su pausa compensadora deja una pausa N-N con ella
  adentro.
- Una P del paciente de más de `P_REFERENCE_MAX` del QRS no se cree (es ruido
  alineado con los R); con P grandes, un latido ancho de su tamaño que nadie
  confirmó se toma por P. Por encima de ~115 lpm, o con la T anterior encima
  de la P, no hay P que medir. En un bloqueo AV completo las P no están
  alineadas con los R y tampoco se miden: si pasan `QUIET_MAX` del escape, un
  paro largo con muchas (45 s con P a 90 lpm) queda ruidoso; y si caen en fase
  con las T de los escapes, tienen "T" y vuelven a contar en el censo.
- Un pop que el firmware confirma a un R-R justo (±`RHYTHM_TOLERANCE`) del R
  vecino se toma por la transición de un colapso: si además cae a menos de una
  pausa de él, la asistolia no sale.
- Una extrasístole que abre la pausa con una T angosta propia (no la del
  paciente) puede acotar desde su T: la pausa sale hasta medio segundo más
  corta.
- Una línea de base de σ ≤ 6 µV llena ventanas `flatline` (menos de 20 µV
  entre percentiles) y no se infiere nada: el gate la llama riel. Los pisos
  reales miden ≥ 32,8 µV (MIT-BIH) y ≥ 79,8 µV (chaleco).
- Con el firmware, un artefacto puede tener una detección en su arranque que
  cae en el interior, y el `SQ_BAD` sigue unos segundos después de la ráfaga:
  de un síncope convulsivo a veces sale solo un lado, o nada.
- Sin flags del firmware, una P de ventana `good` de más de `GOOD_BEAT_MIN`
  sigue cortando el hueco (no hay segundo detector que la descarte). Y con un
  QRS tan chico que el firmware no confirma casi ninguno, todas las ventanas
  quedan `marginal` sin latidos de referencia.

Todo es puro, como el resto de `app/ml`: numpy adentro, `Finding` afuera, con
coordenadas relativas al lote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import maximum_filter1d, minimum_filter1d
from scipy.signal import butter, sosfiltfilt

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityLevel
from app.ml.arrhythmia import PAUSE_ALERT, PAUSE_CRITICAL_SECONDS
from app.ml.contracts import (
    Finding,
    Flags,
    Indices,
    Mask,
    QualityReport,
    QualityWindow,
    Signal,
)
from app.ml.decompression import FLAG_SQI_MASK, FLAG_SQI_SHIFT, SQ_BAD
from app.ml.quality import invalid_samples, remove_mains

#: Medio ancho de la ventana donde se mide la amplitud de un latido: ±60 ms
#: alrededor del R toma el QRS entero y nada de la P ni de la T.
BEAT_HALF_WIDTH_MS = 60
#: Lo que rodea a un latido para su línea de base (`_Block.beat_position`).
BASELINE_MS = 200
#: Ventana del pico a pico local del interior. 120 ms es un QRS ancho: lo que
#: tenga forma de latido en el hueco aparece entero en alguna.
ENVELOPE_MS = 120
#: Latidos y ventanas de referencia que entran, a cada lado.
REFERENCE_SPAN_S = 60.0
#: Sin al menos estos latidos de referencia cerca no hay contra qué medir, y la
#: regla no dice nada.
MIN_REFERENCE_BEATS = 5
#: Los latidos de la referencia por debajo de esta fracción de la mediana no
#: entran: son R mal ubicados o P tomadas por R. Contra la mediana y no contra
#: el percentil 90: en MIT-BIH 114 (23 N de 0,38 mV y 5 V de 1,45 mV a ±60 s) el
#: percentil 90 caía en las V, el piso sacaba a todos los N y la referencia era
#: una V; los N del medio quedaban por debajo de `QUIET_MAX` y dos V acotaban
#: una pausa CRITICAL falsa de 12,7 s con diez latidos adentro.
REFERENCE_FLOOR = 0.4
#: Los latidos de referencia tienen que ser el ritmo del paciente: al menos
#: `CENSUS_MIN` de los R de NeuroKit de las ventanas `good`/`marginal` que miden
#: `CENSUS_FLOOR` o más de la referencia. Si el firmware confirma solo las
#: extrasístoles —QRS normales por debajo de su umbral—, los latidos de
#: referencia son las V, los normales miden ~0,28 de ellas (menos que
#: `QUIET_MAX`) y cada intervalo V-V salía como una pausa CRITICAL con decenas de
#: latidos adentro: MIT-BIH 114 con 0,8 de ganancia (las V eran el 13-18 % de
#: los R de NeuroKit alrededor) y 228 con 0,6. El piso deja afuera las P que
#: NeuroKit marca en un bloqueo AV completo (~0,1 del escape).
CENSUS_FLOOR = 0.2
CENSUS_MIN = 0.5
#: Pero las P de un bloqueo AV completo pueden medir 0,2-0,3 del escape, más que
#: el piso, y no se separan de esos QRS normales ni por amplitud ni por banda
#: (en MIT-BIH 114 con 0,8 de ganancia los N miden 0,17-0,34 de las V y
#: 0,13-0,29 en banda QRS; las P sintéticas, 0,19-0,26 y 0,16-0,28). Con P de
#: 0,25 mV contra un QRS de 1,45 mV ya eran la mitad del censo y el Stokes-Adams
#: no tenía referencia. Lo que las separa es que un QRS tiene su T y una P no:
#: la mediana de `CENSUS_T_WINDOW_S` después de los R del censo que no son de
#: referencia mide, contra su amplitud, 0,43-0,66 en los N de 114 y 228 (y 0,28
#: en los sintéticos), y 0,02-0,06 en las P. Si no llega a `CENSUS_T_MIN`, esos R
#: son P y no cuentan.
CENSUS_T_WINDOW_S = (0.12, 0.45)
CENSUS_T_MIN = 0.15
#: Un escape ventricular chico (0,4-0,45 del sinusal, ancho) no lo confirma el
#: detector del MCU —medido con el detector de producción emulado: 0 de 52— y
#: mide menos que `BOUND_MIN`: la asistolia que cierra no se informaba. Un R de
#: NeuroKit acota igual si abre (o cierra) un tren de escape: `TRAIN_BEATS` R
#: seguidos a `TRAIN_MIN_RR_S` o más —un ritmo de escape va a 20-45 lpm; las P de
#: un bloqueo AV, al ritmo auricular, 60-100— con R-R y amplitudes que no varían
#: más que `TRAIN_SPREAD`, y que mide al menos `TRAIN_MIN` de la referencia. Una
#: recuperación de amplitud (0,28 → 0,5 → 0,75) no es un tren. Tampoco las P
#: bloqueadas de un bloqueo AV vagal, donde la frecuencia auricular baja a 50 lpm
#: o menos: un R del tren no más grande que `P_WAVE_MARGIN` veces la P del
#: paciente es una P, la misma regla del interior. Con P de 0,30-0,35 mV cada P
#: abría y cerraba un "tren", partía la asistolia en pedazos de 1,25-2 s y no se
#: informaba nada.
TRAIN_BEATS = 3
TRAIN_MIN_RR_S = 1.2
TRAIN_SPREAD = 1.3
TRAIN_MIN = 0.25
#: Una cota chica (confirmada o de un tren) con la forma de los latidos del
#: paciente —correlación de ±100 ms con su mediana— es uno de ellos atenuado, no
#: un escape: el colapso de amplitud que se recupera. Medido: las cotas de los
#: colapsos de MIT-BIH 116 y 208 dan 0,92-0,98, y las de 23 colapsos emulados
#: sobre el chaleco, 0,995-1,0; los escapes anchos sintéticos, 0,79, y en MIT-BIH
#: la mediana de las V contra los normales va de -0,34 a 0,90 según el registro.
#: La forma sola no alcanza: un pop de electrodo que el firmware confirma da
#: 0,95-0,98 contra un QRS angosto, y los latidos del paciente que vuelven a 0,4-0,55
#: de su amplitud después de una asistolia (cambio de postura tras el síncope)
#: son exactamente él atenuado. Por eso la guarda de forma se aplica solo a una
#: cota de **transición**: del lado de afuera la siguen (o la preceden) latidos
#: en ritmo —el más cercano a menos de una pausa— que en `RECOVERY_S` llegan a
#: `RECOVERY` veces ella, y está a un R-R de ellos (±`RHYTHM_TOLERANCE`): es
#: un latido más del ritmo. Así es el colapso que se recupera (0,28 → 0,5 →
#: 0,75, o la rampa de los colapsos emulados sobre el chaleco); un pop suelto
#: tiene una pausa de cada lado, o cae a cualquier distancia del R vecino, y un
#: nivel nuevo sostenido no vuelve a crecer.
ATTENUATED_SHAPE = 0.9
SHAPE_HALF_WIDTH_MS = 100
RECOVERY = 1.5
RECOVERY_S = 8.0
RHYTHM_TOLERANCE = 0.25
#: Un R acota la pausa si mide entre estas veces la referencia. 0,6 y no 0,5:
#: los verdaderos de MIT-BIH dan 0,63 o más, y la única pausa falsa de la
#: regla en los 48 registros (208, a los 1383,9 s, una caída de señal de 4,8 s)
#: tenía las cotas en 0,55 y 0,51. En las asistolias sintéticas y en las
#: emuladas sobre el chaleco no cambia ninguna. Un R que el firmware confirmó
#: acota con `GOOD_BEAT_MIN` y sin tope.
BOUND_MIN = 0.6
BOUND_MAX = 3.0
#: Un R de una ventana `good` sin arbitraje del firmware corta el hueco salvo
#: que mida menos que esto, y es también el mínimo de un R confirmado. En las 48
#: grabaciones de MIT-BIH hay 3 R verdaderos de ventanas `good` por debajo,
#: contra 272 detecciones falsas.
GOOD_BEAT_MIN = 0.15
#: El interior del hueco: después de la T del primer latido y antes de la P
#: del segundo. Los mismos márgenes rodean a un transitorio del interior.
INTERIOR_PRE_S = 0.5
INTERIOR_POST_S = 0.3
#: Interior quieto. Asistolias: ≤ 0,17 en sintético (con P de bloqueo AV),
#: ≤ 0,22 en MIT-BIH 232; el piso TP de las ventanas buenas del chaleco da p95
#: 0,26. Ruido: ≥ 0,40 en el chaleco, ≥ 0,91 en NSTDB.
QUIET_MAX = 0.30
#: Y quieto también en la banda del QRS (`QRS_BAND_HZ`), contra la de los
#: latidos de referencia. Con una referencia de latidos anchos (extrasístoles
#: que el firmware confirma solas) un QRS angosto que nadie confirmó mide poco
#: de banda ancha y mucho de pendiente: en el sintético de 114, 0,29 y 0,52.
QUIET_BAND_MAX = 0.30
#: Una cota por debajo de `BOUND_MIN` tiene que sobresalir de lo quieto que
#: cierra como cualquier latido sobresale del hueco: lo quieto, menos que esto
#: de **ella**. Un escape de 0,4× sobre 25 µV de ruido da 0,08; los colapsos de
#: MIT-BIH 116 y 208 con los flags reales, 0,37-0,65. Si la cota no es de
#: transición (ver `ATTENUATED_SHAPE`), las P del paciente que pasan esto no
#: cuentan (`P_WAVE_MARGIN`): en un bloqueo AV paroxístico cerrado por un escape
#: ancho de 0,5× que el firmware confirma, P disociadas de 0,15 mV —un tamaño
#: normal— daban 0,161 contra 0,156 y el paro no se informaba.
QUIET_MAX_UNDER_WEAK_BOUND = QUIET_MAX
#: Transitorios que parten el hueco en vez de anularlo. Más, o más largos, ya
#: es un tramo ruidoso: ahí no se puede afirmar nada. Dos tramos fuertes a menos
#: de `EVENT_MERGE_S` son el mismo evento (el QRS y la T de un escape).
MAX_EVENTS = 2
EVENT_MAX_S = 1.0
EVENT_MERGE_S = 0.5
#: Y un transitorio parte el hueco solo si sobresale: lo quieto mide menos que
#: esta fracción del más débil de ellos. Sobre una asistolia, un escape o un pop
#: dan ≤ 0,1. Con latidos atenuados que rondan `QUIET_MAX` —unos apenas arriba,
#: el resto apenas abajo— no sobresale nada: en MIT-BIH 116 y 208, con diez
#: latidos anotados adentro, 0,76 y 0,61.
EVENT_CONTRAST = 0.5
#: Un transitorio es un latido —y entonces acota los tramos de los dos lados—
#: si en la banda del QRS mide al menos esto de la referencia. Una onda lenta no
#: lo es y el tramo la cruza: la T tardía de un QT largo (0,05-0,11) o una de
#: hiperpotasemia (0,21, que todavía acota: acorta la pausa, nunca la inventa)
#: no le roban medio segundo a la pausa que abre su latido. Los escapes anchos
#: de 0,3-1× dan 0,24-0,82, y un pop de 1 mV, 0,30.
EVENT_BEAT_QRS = 0.15
#: Una P del interior (más corta que `EVENT_MAX_S`) que pasa `QUIET_MAX` no es
#: un transitorio: no cuenta para `MAX_EVENTS` ni para lo quieto, siempre que
#: mida menos que esto. En el chaleco y en MIT-BIH las P de un bloqueo AV miden
#: hasta 0,39 del QRS: con `QUIET_MAX` las de 0,30 o más dejaban el hueco
#: "ruidoso" y un paro ventricular con P (Stokes-Adams) no se informaba. Una
#: onda del tamaño de un latido (`ENGINE_VETO`) ya no es una P.
SLOW_WAVE_MAX = 0.6
#: Qué es una P. Tiene la pendiente de un QRS chico —en banda QRS una P de
#: 0,25-0,40 del QRS mide 0,17-0,28 de la referencia, más que `EVENT_BEAT_QRS`—
#: y por forma no se separa de un latido ancho: en MIT-BIH los escapes `E` son
#: tan redondeados como las P anotadas de QTDB, y una extrasístole ancha de MIT-BIH
#: 228 con deriva, tomada por onda lenta, dejaba una pausa falsa. Lo que la
#: separa es que es la P **del paciente**: la misma que precede a cada latido
#: conducido. Un tramo fuerte no más grande que `P_WAVE_MARGIN` veces esa P es
#: una P. Una deflexión tres veces más grande que la P del paciente no lo es,
#: aunque mida 0,35 del QRS.
P_WAVE_MARGIN = 1.3
#: Dónde se mide la P de cada latido de referencia: de 320 a 80 ms antes del R
#: (un PR de hasta ~240 ms y el ancho de la P), sin pisar la T del latido
#: anterior, que termina a `P_T_END_S` × √RR de su R (la forma de Bazett, con
#: margen: a 84 lpm da 0,39 s, el límite fijo de antes). Con el límite fijo, por
#: encima de 84 lpm no quedaba ningún latido, la P del paciente era cero y un
#: bloqueo AV paroxístico dependiente de la frecuencia (a 95-110 lpm) con P de
#: 0,35 mV dejaba el hueco "ruidoso". Ahora la ventana se acorta por la
#: izquierda hasta `P_MIN_WINDOW_S`: alcanza hasta ~115 lpm.
P_WINDOW_S = (0.32, 0.08)
P_T_END_S = 0.46
P_MIN_WINDOW_S = 0.10
#: La P más grande que se le cree a un paciente, contra su QRS: 0,39 en el
#: chaleco y MIT-BIH. Más es ruido, no una P, y la regla de la P no se aplica.
P_REFERENCE_MAX = 0.4
#: La zona de la T del R que abre un hueco. Un transitorio ahí acota como
#: cualquiera, salvo que sea la T del paciente: esa T (la mediana de la banda
#: QRS de los latidos de referencia a la misma distancia de su R) tiene ahí al
#: menos `T_LIKENESS` de la pendiente del transitorio, y lo que queda de él
#: después de restársela (con ±`T_SHIFT_MS` de juego) ya no tiene la de un
#: latido. Una T tardía y angosta de un QT largo tiene esa pendiente, y le
#: robaba medio segundo a la pausa (3,5 s CRITICAL salían 2,98 HIGH); pero con
#: la zona entera exenta, una extrasístole chica que nadie confirmó, acoplada a
#: 0,5-0,65 s en una bradicardia sinusal, quedaba adentro y su pausa
#: compensadora salía como una pausa CRITICAL de 3 s. Para la plantilla entran
#: los latidos cuyo R siguiente cae `T_CLEAR_S` después de la ventana (que no
#: traiga la P del siguiente); sin `MIN_REFERENCE_BEATS` así, acota.
T_ZONE_S = 0.65
T_SHIFT_MS = 20
T_CLEAR_S = 0.25
T_LIKENESS = 0.5
#: Entre el R que abre y el interior (`INTERIOR_PRE_S`) también puede haber un
#: latido: una extrasístole temprana. Ahí no se mide lo quieto, pero desde
#: `EARLY_FROM_S` (después del QRS del R, aunque sea ancho) un R que el firmware
#: confirmó, o un tramo fuerte con la pendiente de un latido que no es la T del
#: paciente (ni su P), es el latido desde el que corre la pausa. Antes no se
#: miraba: una extrasístole acoplada a 0,3-0,45 s con su pausa compensadora
#: (2,2-2,3 s de R-R real) salía como una pausa de 2,6 s, también del motor, con
#: el firmware confirmándola.
EARLY_FROM_S = 0.25
#: Red y deriva del interior contra las ventanas de referencia vecinas.
#: Verdaderos ≤ 1,26 y ≤ 1,70 en MIT-BIH; la pérdida de contacto de
#: `aviso_ll_ra`, 19,6 y 7,4.
MAINS_MAX = 2.5
DRIFT_MAX = 2.5
#: Holguras absolutas de las dos comparaciones: sin red ni deriva en la
#: referencia (red apagada, señal sintética) el cociente no tiene sentido y
#: cualquier residuo numérico lo haría infinito.
MAINS_SLACK_MV = 0.001
DRIFT_SLACK_MV_S = 0.01
#: Corte del pasabajos que define la deriva lenta.
DRIFT_LOWPASS_HZ = 1.0
#: Interior a partir del cual una pausa con R-R válido no es una pausa sino un
#: latido que NeuroKit no vio (`refine_pauses`)...
ENGINE_VETO = 0.6
#: ...siempre que también lo sea en la banda del QRS. Medido en MIT-BIH: las 46
#: falsas dan 0,80-1,38 y las 72 verdaderas ≤ 0,10. La T de 1,2 mV de una
#: extrasístole (σ 90 ms) da ~0,7 de banda ancha y casi nada acá.
QRS_BAND_HZ = (5.0, 20.0)
ENGINE_VETO_QRS = 0.4
#: Lo que se descarta del principio de una lectura antes de mirar un tramo
#: abierto: el arranque de los filtros de `clean_signal` y de `deinterfere`.
OPEN_SETTLE_S = 1.0
#: Un tramo que llega quieto hasta el final de la lectura se informa desde este
#: largo: sin R que lo cierre no se puede mirar la cota (un colapso de
#: amplitud que se recupera cierra con un latido atenuado), así que se le pide
#: más. Es la asistolia en curso: el bloque que la ve primero avisa ya, y el que
#: lea el R que la cierra la completa (la persistencia empalma las dos).
OPEN_END_MIN_S = 10.0
#: Lo quieto entre un R y una ráfaga de ruido (`_Block.stretches`) se informa
#: desde la duración de una pausa crítica.
NOISE_EDGE_MIN_S = PAUSE_CRITICAL_SECONDS
#: Riel: un segundo de señal **cruda** (red incluida) con menos de
#: `RAIL_FRACTION` × `ml_flatline_uv` entre sus percentiles 5 y 95. Es la
#: `flatline` del gate a la resolución de un segundo: un riel sin `LEAD_OFF`
#: (ADC congelado, entrada en corto, un escalón de continua) que no llena una
#: ventana entera de la grilla no dejaba ninguna fila `flatline`, y la regla
#: —o el motor, con el R-R válido que lo cruza— lo avisaba como una asistolia
#: CRITICAL. Medido: un riel con 0-3 µV de ruido da como mucho 10,8 µV en su
#: segundo más ruidoso (la entrada en corto del ADS1292R, ~1,3 µV de σ, da
#: ~4 µV); la asistolia sintética más limpia de los tests (σ 8 µV) da 23,3 µV
#: como mínimo en 600 segundos; las de MIT-BIH, 32,8 µV en su segundo más
#: quieto, y el piso TP del chaleco, 79,8 µV. Con el umbral entero (20 µV) un
#: segundo de σ 6 µV ya caería la mitad de las veces.
RAIL_SECONDS = 1.0
RAIL_HOP_SECONDS = 0.25
RAIL_FRACTION = 0.6

_GOOD = SignalQualityLevel.GOOD
_MARGINAL = SignalQualityLevel.MARGINAL
#: Motivos de ventana que son el hardware diciendo que no hay señal.
_HARDWARE_REASONS = frozenset({"lead_off", "saturated"})
#: Motivos de las ventanas `bad` que salen como `noise_burst` (`pipeline`).
_NOISE_REASONS = frozenset({"psqi", "ksqi", "bassqi", "no_beats"})


@dataclass(frozen=True, slots=True)
class GapEvidence:
    """Lo que la regla lee del bloque, ya calculado por `pipeline.analyze_batch`."""

    signal: Signal
    flags: Flags
    #: La señal de `rpeak_detection.clean_signal`, la misma de los R.
    cleaned: Signal
    #: Los R de NeuroKit.
    rpeaks: Indices
    #: Los R del firmware ya compensados (`compensate_firmware_peaks`).
    firmware_peaks: Indices
    report: QualityReport
    #: Ventanas `good` sin el entorno de los empalmes: la máscara de `build_rr`.
    analyzable: Mask
    #: Falso en el entorno de cada empalme (`quality.exclude_splices`).
    splice_free: Mask
    sample_rate: int
    #: La del bSQI: un R de NeuroKit con un R del firmware a esta distancia es un
    #: latido que vieron los dos detectores.
    tolerance_samples: int
    #: Contexto izquierdo del bloque. Mayor que cero, la lectura empieza en medio
    #: de su corrida y un tramo quieto puede venir de antes (`openStart`).
    context_samples: int = 0
    #: Contexto derecho del bloque.
    lookahead_samples: int = 0
    #: El umbral de `flatline` del gate (`QualityThresholds.flatline_mv`), en mV.
    flatline_mv: float = 0.020


@dataclass(frozen=True, slots=True)
class _Reference:
    """Contra qué se mide un hueco: los latidos del paciente que lo rodean."""

    #: Pico a pico (±`BEAT_HALF_WIDTH_MS`) de los latidos: cotas e interior.
    amplitude: float
    #: El mismo, en `QRS_BAND_HZ`: si algo del interior tiene la pendiente de un QRS.
    band: float
    #: El pico a pico de su P (`P_WINDOW_S` antes de cada R): hasta dónde algo
    #: del interior puede ser una P.
    p_wave: float
    #: La misma P medida latido a latido con su ruido, como se mide una P suelta
    #: del interior (`_Block.only_p_waves`).
    p_seen: float
    #: Índices (en los R de NeuroKit) de los latidos de referencia: la plantilla
    #: de su T (`_Block.own_t_wave`).
    beats: np.ndarray


@dataclass(frozen=True, slots=True)
class _Stretch:
    """Un tramo quieto candidato: sus dos cotas y los pedazos de interior que se midieron."""

    first: int
    second: int
    #: `[inicio, fin)` de cada pedazo quieto, sin los transitorios que el tramo cruza.
    parts: tuple[tuple[int, int], ...]
    #: Transitorios que tenía el hueco del que sale el tramo.
    events: int = 0
    #: Lado sin R: el tramo empieza al final de una ráfaga de ruido, o termina
    #: donde empieza una o donde termina la lectura. La pausa duró por lo menos
    #: eso (`openStart` / `openEnd`).
    open_start: bool = False
    open_end: bool = False


def _drifting(window: QualityWindow) -> bool:
    """Una ventana `bad` solo por basSQI: la deriva lenta. Para la regla se lee
    como un ECG, porque todo se mide sobre la señal limpia, que ya no la tiene
    (`clean_signal` corta en 0,5 Hz). Con una respiración de 0,3 mV a 15-18 rpm
    todas las ventanas salían así y una asistolia no tenía referencia."""
    return window.level is SignalQualityLevel.BAD and window.reason == "bassqi"


def _samples(milliseconds: float, sample_rate: int) -> int:
    return max(int(round(milliseconds * sample_rate / 1000.0)), 1)


def _cumulative(mask: Mask) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(mask, dtype=np.int64)))


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values, dtype=np.float64)))) if values.size else 0.0


def _envelope(segment: np.ndarray, size: int) -> np.ndarray:
    """Pico a pico local de `size` muestras, centrado."""
    size = min(size, segment.size)
    high: np.ndarray = maximum_filter1d(segment, size, mode="nearest")
    low: np.ndarray = minimum_filter1d(segment, size, mode="nearest")
    spread: np.ndarray = np.subtract(high, low)
    return spread


def _runs(mask: Mask) -> list[tuple[int, int]]:
    """Los tramos `[inicio, fin)` donde `mask` es verdadero."""
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(
        zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True)
    )


def rail_mask(signal: Signal, sample_rate: int, threshold_mv: float) -> Mask:
    """Las muestras de un riel: segundos de señal cruda casi constante.

    Cada segundo (con paso de `RAIL_HOP_SECONDS`) cuyo rango entre los
    percentiles 5 y 95 queda por debajo de `threshold_mv` se marca entero. Los
    segundos con muestras no finitas no se miran: ya son hardware
    (`invalid_samples`).
    """
    n = int(signal.size)
    mask = np.zeros(n, dtype=bool)
    width = max(int(RAIL_SECONDS * sample_rate), 2)
    if n < width or threshold_mv <= 0:
        return mask
    hop = max(int(RAIL_HOP_SECONDS * sample_rate), 1)
    starts = np.unique(np.append(np.arange(0, n - width + 1, hop), n - width))
    windows = sliding_window_view(np.asarray(signal, dtype=np.float64), width)[starts]
    finite = np.isfinite(windows).all(axis=1)
    low, high = np.percentile(np.where(np.isfinite(windows), windows, 0.0), [5.0, 95.0], axis=1)
    for start in starts[finite & (high - low < threshold_mv)].tolist():
        mask[start : start + width] = True
    return mask


def _near(peaks: np.ndarray, others: np.ndarray, tolerance: int) -> Mask:
    """Por cada pico, si alguno de `others` cae a `tolerance` muestras o menos."""
    if peaks.size == 0 or others.size == 0:
        return np.zeros(peaks.size, dtype=bool)
    others = np.sort(others)
    position = np.searchsorted(others, peaks)
    left = others[np.clip(position - 1, 0, others.size - 1)]
    right = others[np.clip(position, 0, others.size - 1)]
    near: Mask = (np.abs(peaks - left) <= tolerance) | (np.abs(right - peaks) <= tolerance)
    return near


class _Block:
    """Los rasgos de un bloque, calculados una vez y a demanda."""

    def __init__(self, evidence: GapEvidence) -> None:
        self.evidence = evidence
        rate = evidence.sample_rate
        self.n = int(evidence.cleaned.size)
        self.cleaned = evidence.cleaned.astype(np.float64)
        self.peaks = np.asarray(evidence.rpeaks, dtype=np.int64)
        self.span = int(REFERENCE_SPAN_S * rate)
        self.envelope = _samples(ENVELOPE_MS, rate)
        self.beat_width = 2 * _samples(BEAT_HALF_WIDTH_MS, rate)
        self.firmware = np.sort(np.asarray(evidence.firmware_peaks, dtype=np.int64))

        windows = evidence.report.windows
        marginal = self._window_mask(windows, lambda window: window.level is _MARGINAL)
        readable = self._window_mask(windows, lambda window: window.level in (_GOOD, _MARGINAL))
        drifting = self._window_mask(windows, _drifting)
        arbitrated = self._window_mask(windows, lambda window: window.bsqi is not None)
        if self.peaks.size:
            index = np.clip(self.peaks, 0, self.n - 1)
            self.amplitude = self._peak_to_peak(self.cleaned)[index]
            self.good_beat = evidence.analyzable[index]
            self.arbitrated = arbitrated[index]
            self.confirmed = _near(self.peaks, self.firmware, evidence.tolerance_samples)
            self.train = _train(self.peaks, self.amplitude, self.cleaned, rate, self.envelope)
            # Los latidos del paciente: los `good`, y los de ventanas `marginal`
            # —o `bad` solo por la deriva (`_drifting`)— que los dos
            # detectores vieron.
            self.reference_beat = self.good_beat | (
                (marginal[index] | drifting[index]) & evidence.splice_free[index] & self.confirmed
            )
            #: Los R de NeuroKit de las ventanas que se leen como un ECG: el
            #: censo contra el que se mide si los latidos de referencia son el
            #: ritmo del paciente (`reference_beats`).
            self.readable_beat = (readable[index] | drifting[index]) & evidence.splice_free[index]
        else:
            self.amplitude = np.empty(0, dtype=np.float64)
            self.good_beat = np.empty(0, dtype=bool)
            self.arbitrated = np.empty(0, dtype=bool)
            self.confirmed = np.empty(0, dtype=bool)
            self.train = np.empty(0, dtype=bool)
            self.reference_beat = np.empty(0, dtype=bool)
            self.readable_beat = np.empty(0, dtype=bool)

        self.hardware = _cumulative(invalid_samples(evidence.signal, evidence.flags))
        self.hardware_windows = _cumulative(
            self._window_mask(windows, lambda window: window.reason in _HARDWARE_REASONS)
        )
        self.firmware_windows = _cumulative(
            self._window_mask(windows, lambda window: window.reason == "firmware_sqi")
        )
        #: El SQI del firmware muestra a muestra: lo que miran los tramos al
        #: borde de una ráfaga de ruido. Una ventana `firmware_sqi` es la que
        #: tiene la mitad de sus muestras `SQ_BAD`, y la de una asistolia que
        #: termina en una convulsión lo es por el artefacto, no por los segundos
        #: quietos de antes.
        sqi = (np.asarray(evidence.flags) & FLAG_SQI_MASK) >> FLAG_SQI_SHIFT
        self.firmware_bad = _cumulative(sqi == SQ_BAD)
        self.flatline_windows = _cumulative(
            self._window_mask(windows, lambda window: window.reason == "flatline")
        )
        self.splices = _cumulative(~evidence.splice_free)
        self.rails = _cumulative(
            rail_mask(evidence.signal, rate, RAIL_FRACTION * evidence.flatline_mv)
        )
        #: Las ventanas contra las que se mide el contacto: las que se leen como
        #: un ECG, `good` o `marginal` (pasaron pSQI, kSQI y basSQI).
        self.reference_windows = [
            (window.start_sample, window.start_sample + window.length_samples)
            for window in windows
            if window.level in (_GOOD, _MARGINAL) or _drifting(window)
        ]
        #: Cuáles de ellas son `bad` solo por la deriva: entran si no hay otras.
        self.reference_drifting = np.array(
            [
                _drifting(window)
                for window in windows
                if window.level in (_GOOD, _MARGINAL) or _drifting(window)
            ],
            dtype=bool,
        )
        self.reference_centers = np.array(
            [(start + end) // 2 for start, end in self.reference_windows], dtype=np.int64
        )
        self._contact: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._qrs: tuple[np.ndarray, np.ndarray] | None = None

    def _window_mask(
        self, windows: tuple[QualityWindow, ...], keep: Callable[[QualityWindow], bool]
    ) -> Mask:
        mask = np.zeros(self.n, dtype=bool)
        for window in windows:
            if keep(window):
                mask[window.start_sample : window.start_sample + window.length_samples] = True
        return mask

    def _peak_to_peak(self, values: np.ndarray) -> np.ndarray:
        """Pico a pico de ±`BEAT_HALF_WIDTH_MS` en cada muestra."""
        return _envelope(values, self.beat_width)

    @staticmethod
    def marked(cumulative: np.ndarray, start: int, end: int) -> bool:
        """Si hay alguna muestra marcada en `[start, end)`."""
        return bool(cumulative[max(start, 0)] < cumulative[max(end, 0)])

    def reference_beats(self, spans: tuple[tuple[int, int], ...]) -> np.ndarray | None:
        """Índices de los latidos de referencia de `spans` (`[desde, hasta]`), o None.

        La población dominante: sin los que miden menos de `REFERENCE_FLOOR`
        de la mediana. Sin `MIN_REFERENCE_BEATS` no hay referencia, y tampoco
        si no son el ritmo del paciente: menos de `CENSUS_MIN` de los R de
        NeuroKit de las ventanas legibles que miden al menos `CENSUS_FLOOR` de
        ella.
        """
        chosen: list[np.ndarray] = []
        census: list[np.ndarray] = []
        for low, high in spans:
            first = int(np.searchsorted(self.peaks, low, side="left"))
            last = int(np.searchsorted(self.peaks, high, side="right"))
            chosen.append(np.arange(first, last)[self.reference_beat[first:last]])
            census.append(np.arange(first, last)[self.readable_beat[first:last]])
        index = np.unique(np.concatenate(chosen)) if chosen else np.empty(0, dtype=np.int64)
        if index.size < MIN_REFERENCE_BEATS:
            return None
        values = self.amplitude[index]
        index = index[values >= REFERENCE_FLOOR * float(np.median(values))]
        if index.size < MIN_REFERENCE_BEATS:
            return None
        # Con el firmware arbitrando, los latidos son los que también vio él:
        # en una asistolia de dos minutos la referencia local de un R falso de
        # NeuroKit eran otros R falsos (0,03 mV) y dos latidos, y el falso
        # medía 0,93 de "eso" y partía la asistolia.
        if (
            self.firmware.size
            and int(np.count_nonzero(self.confirmed[index])) < MIN_REFERENCE_BEATS
        ):
            return None
        pool = np.unique(np.concatenate(census))
        pool = pool[self.amplitude[pool] >= CENSUS_FLOOR * float(np.median(self.amplitude[index]))]
        if (
            pool.size
            and float(np.mean(self.reference_beat[pool])) < CENSUS_MIN
            and self._has_t_wave(pool[~self.reference_beat[pool]])
        ):
            return None
        return index

    def _has_t_wave(self, members: np.ndarray) -> bool:
        """Si los R `members` tienen una T: la mediana, muestra a muestra, de
        `CENSUS_T_WINDOW_S` después de cada uno mide al menos `CENSUS_T_MIN` de
        su amplitud. Son QRS (con su T), no P. Con menos de
        `MIN_REFERENCE_BEATS` no se puede decir: cuentan como QRS."""
        rate = self.evidence.sample_rate
        low, high = (int(seconds * rate) for seconds in CENSUS_T_WINDOW_S)
        members = members[self.peaks[members] + high <= self.n]
        if members.size < MIN_REFERENCE_BEATS:
            return True
        windows = np.stack(
            [self.cleaned[peak + low : peak + high] for peak in self.peaks[members].tolist()]
        )
        template = np.median(windows, axis=0)
        size = float(np.median(self.amplitude[members]))
        return float(template.max() - template.min()) >= CENSUS_T_MIN * size

    def reference(self, spans: tuple[tuple[int, int], ...]) -> float | None:
        """Amplitud típica de los latidos del paciente en `spans`."""
        index = self.reference_beats(spans)
        if index is None:
            return None
        median = float(np.median(self.amplitude[index]))
        return median if median > 0 else None

    def gap_reference(self, first: int, second: int) -> _Reference | None:
        """La referencia de un hueco (`_Reference`), o None si no hay latidos."""
        index = self.reference_beats(self.gap_spans(first, second))
        if index is None:
            return None
        amplitude = float(np.median(self.amplitude[index]))
        if amplitude <= 0:
            return None
        _, band_amplitude = self._qrs_band()
        band = float(np.median(band_amplitude[np.clip(self.peaks[index], 0, self.n - 1)]))
        p_wave, p_seen = self._p_wave(index)
        return _Reference(amplitude, band, p_wave, p_seen, index)

    def p_wave(self, spans: tuple[tuple[int, int], ...]) -> float:
        """La P del paciente (`_p_wave`) de los latidos de referencia de `spans`."""
        index = self.reference_beats(spans)
        return 0.0 if index is None else self._p_wave(index)[0]

    def _p_wave(self, index: np.ndarray) -> tuple[float, float]:
        """Pico a pico de la P en el promedio de los latidos de `index`, y el de
        cada una con su ruido (la mediana).

        La mediana, muestra a muestra, de `P_WINDOW_S` antes de cada R: el ruido
        no está sincronizado con el R y se cancela; la P sí. La ventana empieza
        donde termina la T del latido anterior (`P_T_END_S`) si eso es después
        de `P_WINDOW_S`: la misma para todos, la del cuartil más corto, y entran
        los latidos que la dejan libre. Con menos de `MIN_REFERENCE_BEATS`, o con
        una ventana de menos de `P_MIN_WINDOW_S`, no hay P; y una de más de
        `P_REFERENCE_MAX` no es una P sino ruido alineado con los R (NSTDB a
        -6 dB da hasta 0,73): cero.
        """
        rate = self.evidence.sample_rate
        peaks = self.peaks[index]
        previous = self.peaks[np.maximum(index - 1, 0)]
        rr = np.where(index > 0, (peaks - previous) / rate, 0.0)
        # Lo que queda entre el final de la T anterior y el R.
        room = rr - P_T_END_S * np.sqrt(rr)
        usable = (index > 0) & (room >= P_WINDOW_S[1] + P_MIN_WINDOW_S)
        if int(np.count_nonzero(usable)) < MIN_REFERENCE_BEATS:
            return 0.0, 0.0
        earliest_s = min(P_WINDOW_S[0], float(np.percentile(room[usable], 25)))
        earliest, latest = int(earliest_s * rate), int(P_WINDOW_S[1] * rate)
        keep = usable & (room >= earliest_s) & (peaks - earliest >= 0)
        if int(np.count_nonzero(keep)) < MIN_REFERENCE_BEATS:
            return 0.0, 0.0
        windows = np.stack(
            [self.cleaned[peak - earliest : peak - latest] for peak in peaks[keep].tolist()]
        )
        average = np.median(windows, axis=0)
        p_wave = float(average.max() - average.min())
        if p_wave > P_REFERENCE_MAX * float(np.median(self.amplitude[index])):
            return 0.0, 0.0
        # La misma P vista latido a latido, con el ruido: lo que mide una P
        # suelta del interior con el mismo pico a pico local.
        seen = float(np.median([np.max(_envelope(window, self.envelope)) for window in windows]))
        return p_wave, max(seen, p_wave)

    def gap_spans(self, first: int, second: int) -> tuple[tuple[int, int], ...]:
        """Lo que rodea un hueco: `REFERENCE_SPAN_S` antes de su primer R y
        después del segundo. Nunca su centro, que en un hueco largo es el hueco."""
        return ((first - self.span, first), (second, second + self.span))

    def bounds(self, index: int, references: _Reference) -> bool:
        """Si el R `index` puede cerrar un hueco medido contra `references`."""
        size = float(self.amplitude[index])
        ratio = size / references.amplitude
        return (
            BOUND_MIN <= ratio <= BOUND_MAX
            or (bool(self.confirmed[index]) and ratio >= GOOD_BEAT_MIN)
            or (
                bool(self.train[index])
                and TRAIN_MIN <= ratio <= BOUND_MAX
                and size > P_WAVE_MARGIN * references.p_wave
            )
        )

    def transitional(self, index: int, step: int, cut: Mask, reach: int) -> bool:
        """Si la cota chica `index` es de transición (`ATTENUATED_SHAPE`).

        Del lado de afuera (`step` = 1 si cierra el hueco, -1 si lo abre): el R
        que corta más cercano está a `reach` muestras o menos y a un R-R del
        ritmo de los que siguen (`RHYTHM_TOLERANCE`), y en `RECOVERY_S` los que
        cortan llegan a `RECOVERY` veces ella.
        """
        peak = int(self.peaks[index])
        horizon = int(RECOVERY_S * self.evidence.sample_rate)
        cuts = np.flatnonzero(cut)
        outer = cuts[cuts > index] if step > 0 else cuts[cuts < index][::-1]
        outer = outer[np.abs(self.peaks[outer] - peak) <= horizon]
        if outer.size == 0:
            return False
        nearest = abs(int(self.peaks[outer[0]]) - peak)
        if nearest > reach:
            return False
        if outer.size >= 3:
            rhythm = float(np.median(np.abs(np.diff(self.peaks[outer]))))
            if abs(nearest - rhythm) > RHYTHM_TOLERANCE * rhythm:
                return False
        return float(np.max(self.amplitude[outer])) >= RECOVERY * float(self.amplitude[index])

    def likeness(self, peak: int, first: int, second: int) -> float:
        """Correlación de la forma del latido de `peak` (±`SHAPE_HALF_WIDTH_MS`,
        sin media) con la mediana de los latidos de referencia del hueco."""
        index = self.reference_beats(self.gap_spans(first, second))
        half = _samples(SHAPE_HALF_WIDTH_MS, self.evidence.sample_rate)
        if index is None or peak - half < 0 or peak + half + 1 > self.n:
            return 0.0
        beats = [
            self.cleaned[int(center) - half : int(center) + half + 1]
            for center in self.peaks[index].tolist()
            if half <= int(center) < self.n - half - 1
        ]
        if not beats:
            return 0.0
        template = np.median(np.stack(beats), axis=0)
        template = template - template.mean()
        beat = self.cleaned[peak - half : peak + half + 1]
        beat = beat - beat.mean()
        scale = float(np.linalg.norm(template) * np.linalg.norm(beat))
        return float(np.dot(template, beat)) / scale if scale > 0 else 0.0

    def missing(self, first: int, second: int, start: int, end: int, *, edge: bool = False) -> bool:
        """Si falta señal entre `first` y `second` (interior `[start, end)`): nunca
        se infiere una pausa a través de ella. Una muestra `LEAD_OFF`,
        `ADC_SATURATED` o no finita, una ventana `lead_off`/`saturated`, una
        muestra `SQ_BAD` del firmware en el interior —de una ventana
        `firmware_sqi`, salvo en el tramo al borde de una ráfaga (`edge`): la
        ventana la marca el artefacto—, un empalme, un riel o una ventana
        `flatline` en el interior; y un R del firmware en el interior es un
        latido que vio."""
        firmware = self.firmware
        return (
            self.marked(self.hardware, first, second + 1)
            or self.marked(self.hardware_windows, first, second + 1)
            or (
                self.marked(self.firmware_bad, start, end)
                and (edge or self.marked(self.firmware_windows, first, second + 1))
            )
            or self.marked(self.splices, first, second + 1)
            or self.marked(self.rails, first, second + 1)
            or self.marked(self.flatline_windows, start, end)
            or bool(firmware.size and ((firmware >= start) & (firmware < end)).any())
        )

    def interior(self, first: int, second: int) -> tuple[int, int]:
        rate = self.evidence.sample_rate
        return first + int(INTERIOR_PRE_S * rate), second - int(INTERIOR_POST_S * rate)

    def _qrs_band(self) -> tuple[np.ndarray, np.ndarray]:
        """La señal limpia en `QRS_BAND_HZ` y su pico a pico de latido. Una vez por bloque."""
        if self._qrs is None:
            rate = self.evidence.sample_rate
            sos = butter(2, QRS_BAND_HZ, btype="band", fs=float(rate), output="sos")
            if self.n > 3 * (2 * sos.shape[0] + 1):
                band = sosfiltfilt(sos, self.cleaned)
            else:
                band = np.zeros(self.n, dtype=np.float64)
            self._qrs = (band, self._peak_to_peak(band))
        return self._qrs

    def beat_inside(self, first: int, second: int) -> bool:
        """Si el interior de un R-R tiene un latido que NeuroKit no vio: del
        tamaño de uno (`ENGINE_VETO`) y con su pendiente (`ENGINE_VETO_QRS`)."""
        start, end = self.interior(first, second)
        if end <= start:
            return False
        references = self.gap_reference(first, second)
        if references is None:
            return False
        reference, band_reference = references.amplitude, references.band
        if float(np.max(_envelope(self.cleaned[start:end], self.envelope))) < (
            ENGINE_VETO * reference
        ):
            return False
        if band_reference <= 0:
            return True
        band = self._qrs_band()[0]
        loudest = float(np.max(_envelope(band[start:end], self.envelope)))
        return loudest >= ENGINE_VETO_QRS * band_reference

    def beat_position(self, at: int) -> int:
        """Dónde está el R de un latido ubicado en `at` (por su pendiente, o por
        el firmware): lo que más se aparta, a ±`BEAT_HALF_WIDTH_MS`, de la línea
        de base —la mediana de ±`BASELINE_MS`, que un QRS ancho no llena—. La
        pendiente de un QRS ancho cae hasta 60 ms antes de su pico, y una pausa
        se mide de R a R: una extrasístole con 2,45 s de pausa compensadora
        salía de 2,51."""
        half = self.beat_width // 2
        wide = _samples(BASELINE_MS, self.evidence.sample_rate)
        baseline = float(np.median(self.cleaned[max(at - wide, 0) : at + wide + 1]))
        low, high = max(at - half, 0), min(at + half + 1, self.n)
        segment = self.cleaned[low:high]
        return low + int(np.argmax(np.abs(segment - baseline)))

    def own_t_wave(self, first: int, at: int, references: _Reference) -> bool:
        """Si lo que hay en `at` es la T del latido de `first` (`T_ZONE_S`).

        Se le resta a la banda QRS alrededor de `at` la mediana de la de los
        latidos de referencia a la misma distancia de su R, con ±`T_SHIFT_MS`
        de juego: si lo que queda no tiene la pendiente de un latido
        (`EVENT_BEAT_QRS`), es su T. Sin `MIN_REFERENCE_BEATS` latidos cuyo R
        siguiente caiga `T_CLEAR_S` después de la ventana no se puede decir: no.
        """
        rate = self.evidence.sample_rate
        shift = _samples(T_SHIFT_MS, rate)
        low = at - first - self.envelope - shift
        high = at - first + self.envelope + shift
        if low < 0 or first + high > self.n:
            return False
        # El latido siguiente es el siguiente de referencia (o uno que vio el
        # firmware): NeuroKit marca a veces la T angosta de un QT largo como R.
        peaks = self.peaks[references.beats]
        clear = high + int(T_CLEAR_S * rate)
        following = np.append(peaks[1:], self.n)
        confirmed = np.searchsorted(self.firmware, peaks + self.evidence.tolerance_samples)
        next_confirmed = np.append(self.firmware, self.n)[confirmed]
        keep = (
            (np.minimum(following, next_confirmed) - peaks >= clear)
            & (peaks + high <= self.n)
            & (peaks != first)
        )
        if int(np.count_nonzero(keep)) < MIN_REFERENCE_BEATS:
            return False
        band = self._qrs_band()[0]
        template = np.median(
            np.stack([band[peak + low : peak + high] for peak in peaks[keep].tolist()]), axis=0
        )
        # Si la T del paciente es ahí mucho más lenta que lo que hay, no es ella:
        # una extrasístole chica y ancha mide apenas más que `EVENT_BEAT_QRS`, y
        # restarle una plantilla chata no la explica (`T_LIKENESS`).
        beat_band = EVENT_BEAT_QRS * references.band
        width = high - low - 2 * shift
        segment = band[first + low + shift : first + low + shift + width]
        own = float(np.max(_envelope(template[shift : shift + width], self.envelope)))
        if own < T_LIKENESS * float(np.max(_envelope(segment, self.envelope))):
            return False
        residual = min(
            float(np.max(_envelope(segment - template[offset : offset + width], self.envelope)))
            for offset in range(0, 2 * shift + 1, max(shift // 5, 1))
        )
        return residual < beat_band

    def early_beat(self, first: int, second: int, references: _Reference) -> int | None:
        """El último latido entre `EARLY_FROM_S` después del R `first` y el
        interior (`INTERIOR_PRE_S`), o None.

        Un R del firmware, o un tramo fuerte con la pendiente de un latido
        (`EVENT_BEAT_QRS`) que no es una P del paciente (`P_WAVE_MARGIN`);
        ninguno de los dos si es la T del R que abre (`own_t_wave`). Se ubica en
        su R (`beat_position`) o donde es más empinado, lo que quede después: la
        pausa corre desde ahí y nunca se alarga por ubicarlo mal.
        """
        rate = self.evidence.sample_rate
        low = first + int(EARLY_FROM_S * rate)
        high = min(first + int(INTERIOR_PRE_S * rate), second - int(INTERIOR_POST_S * rate))
        if high <= low:
            return None
        found = [
            max(peak, self.beat_position(peak))
            for peak in self.firmware[(self.firmware >= low) & (self.firmware < high)].tolist()
            if not self.own_t_wave(first, peak, references)
        ]
        reference, band_reference = references.amplitude, references.band
        # Sin nada de antes de `low`: el pico a pico local de ahí todavía ve el
        # QRS del R que abre (la S de una extrasístole ancha llega a 0,19 s).
        around = slice(low, min(high + self.envelope, self.n))
        envelope = _envelope(self.cleaned[around], self.envelope)[: high - low]
        band = self._qrs_band()[0][around]
        band_envelope = _envelope(band, self.envelope)[: high - low]
        band = band[: high - low]
        loud = (envelope >= QUIET_MAX * reference) | (
            band_envelope >= QUIET_BAND_MAX * band_reference
        )
        p_wave = P_WAVE_MARGIN * references.p_wave
        for run_low, run_high in _runs(loud):
            size = float(np.max(envelope[run_low:run_high]))
            if size < SLOW_WAVE_MAX * reference and size <= p_wave:
                continue
            if float(np.max(band_envelope[run_low:run_high])) < EVENT_BEAT_QRS * band_reference:
                continue
            anchor = low + run_low + int(np.argmax(np.abs(band[run_low:run_high])))
            if not self.own_t_wave(first, anchor, references):
                found.append(max(anchor, self.beat_position(anchor)))
        # Solo lo que cae antes del interior: lo de después es un transitorio
        # del interior (`stretches`), aunque su pico a pico empiece antes.
        found = [peak for peak in found if low <= peak < high]
        return max(found) if found else None

    def early_bound(self, peak: int, reference: float) -> float | None:
        """Si la extrasístole temprana `peak` acota como un R: None si mide al
        menos `BOUND_MIN` de la referencia (o si el firmware la confirmó y mide
        `GOOD_BEAT_MIN`); si no, su amplitud, y es una cota chica, con las
        guardas de una (`_stretch_pause`)."""
        size = _anchor_amplitude(self, peak)
        confirmed = bool(
            self.firmware.size
            and np.min(np.abs(self.firmware - peak)) <= self.evidence.tolerance_samples
        )
        if size >= BOUND_MIN * reference or (confirmed and size >= GOOD_BEAT_MIN * reference):
            return None
        return size

    def only_p_waves(self, parts: tuple[tuple[int, int], ...], floor: float, p_wave: float) -> bool:
        """Si todo lo que pasa `floor` en los pedazos `parts` son P del paciente:
        tramos más cortos que `EVENT_MAX_S` que no miden más que `p_wave`."""
        if p_wave <= floor:
            return False
        longest = int(EVENT_MAX_S * self.evidence.sample_rate)
        for low, high in parts:
            envelope = _envelope(self.cleaned[low:high], self.envelope)
            for run_low, run_high in _runs(envelope > floor):
                if (
                    run_high - run_low > longest
                    or float(np.max(envelope[run_low:run_high])) > p_wave
                ):
                    return False
        return True

    def stretches(
        self, first: int, second: int, start: int, end: int, references: _Reference
    ) -> tuple[list[_Stretch] | None, list[_Stretch]]:
        """Los tramos quietos de un hueco, partido en sus transitorios (None si no
        lo está), y los de sus bordes cuando lo anula una ráfaga de ruido.

        `[start, end)` es el interior y `references`, la de `gap_reference`.
        Lo que pasa `QUIET_MAX` es un tramo fuerte. Si es corto, más chico que
        `SLOW_WAVE_MAX` y no más grande que la P del paciente (`P_WAVE_MARGIN`),
        es una P —la de un bloqueo AV— y no cuenta: queda adentro del tramo,
        fuera de lo que se mide. El resto son
        transitorios, y los que distan menos de `EVENT_MERGE_S` son uno. Más de
        `MAX_EVENTS`, uno de más de `EVENT_MAX_S` o uno que no sobresale de lo
        quieto (`EVENT_CONTRAST`) es un tramo ruidoso. Los que son latidos
        (`EVENT_BEAT_QRS`) acotan; el resto queda adentro del tramo.

        Un hueco ruidoso no se puede afirmar entero, pero si el primero de sus
        transitorios es una ráfaga (más de `EVENT_MAX_S`), lo quieto entre el R
        que abre y la ráfaga sí: es un tramo abierto a la derecha. Igual del
        otro lado. Es la asistolia larga: a los 8-10 s el paciente se desmaya,
        cae o convulsiona, y el artefacto anulaba la pausa entera.
        """
        rate = self.evidence.sample_rate
        reference, band_reference = references.amplitude, references.band
        p_wave = P_WAVE_MARGIN * references.p_wave
        envelope = _envelope(self.cleaned[start:end], self.envelope)
        band = self._qrs_band()[0][start:end]
        band_envelope = _envelope(band, self.envelope)
        # Quieto también en la banda del QRS: con una referencia de latidos
        # anchos (extrasístoles, escapes) un QRS angosto que nadie confirmó
        # mide poco de banda ancha y mucho de pendiente.
        loud = (envelope >= QUIET_MAX * reference) | (
            band_envelope >= QUIET_BAND_MAX * band_reference
        )
        if not loud.any():
            return [_Stretch(first, second, ((start, end),))], []
        beat_band = EVENT_BEAT_QRS * band_reference
        slow: list[tuple[int, int]] = []
        events: list[tuple[int, int]] = []
        #: Los tramos fuertes que forman cada transitorio.
        pieces: list[list[tuple[int, int]]] = []
        for low, high in _runs(loud):
            size = float(np.max(envelope[low:high]))
            if (
                high - low <= int(EVENT_MAX_S * rate)
                and size < SLOW_WAVE_MAX * reference
                and (size <= p_wave)
            ):
                slow.append((low, high))
            elif events and low - events[-1][1] < int(EVENT_MERGE_S * rate):
                events[-1] = (events[-1][0], high)
                pieces[-1].append((low, high))
            else:
                events.append((low, high))
                pieces.append([(low, high)])
        # Lo quieto: sin las ondas lentas y sin los transitorios, con los
        # márgenes de un latido alrededor de estos.
        before, after = int(INTERIOR_POST_S * rate), int(INTERIOR_PRE_S * rate)
        measured = np.ones(end - start, dtype=bool)
        for low, high in slow:
            measured[low:high] = False
        for low, high in events:
            measured[max(low - before, 0) : high + after] = False
        longest = int(EVENT_MAX_S * rate)
        if len(events) > MAX_EVENTS or any(high - low > longest for low, high in events):
            return None, self._edges(first, second, start, events, measured, longest)
        quiet = max(
            (float(np.max(envelope[low:high])) for low, high in _runs(measured)), default=0.0
        )
        if events and quiet >= EVENT_CONTRAST * min(
            float(np.max(envelope[low:high])) for low, high in events
        ):
            return None, self._edges(first, second, start, events, measured, longest)
        # Cada latido se ubica donde es más empinado: en la banda del QRS la
        # deriva lenta no cuenta, y lo que domina es su componente más rápida.
        # En la zona de la T del R que abre (`T_ZONE_S`) no acota si es su T
        # (`own_t_wave`): una T tardía y angosta de un QT largo tiene la
        # pendiente de un latido y le robaba medio segundo a la pausa. Una
        # extrasístole ahí sí acota. El tramo de la izquierda llega hasta el
        # primer latido del transitorio y el de la derecha sale del último,
        # desde su R si queda después (`beat_position`): ninguno de los dos se
        # alarga por ubicarlo mal.
        anchors = [(first, first)]
        for parts in pieces:
            beats = []
            for low, high in parts:
                anchor = start + low + int(np.argmax(np.abs(band[low:high])))
                if float(np.max(band_envelope[low:high])) >= beat_band and not (
                    anchor - first <= T_ZONE_S * rate and self.own_t_wave(first, anchor, references)
                ):
                    beats.append(anchor)
            if beats:
                anchors.append((beats[0], max(beats[-1], self.beat_position(beats[-1]))))
        anchors.append((second, second))
        stretches: list[_Stretch] = []
        for (_, left), (right, _) in zip(anchors[:-1], anchors[1:], strict=True):
            low, high = max(left - start, 0), min(right - start, end - start)
            kept = tuple((start + low + a, start + low + b) for a, b in _runs(measured[low:high]))
            if kept:
                stretches.append(_Stretch(left, right, kept, events=len(events)))
        return stretches, []

    @staticmethod
    def _edges(
        first: int,
        second: int,
        start: int,
        events: list[tuple[int, int]],
        measured: Mask,
        longest: int,
    ) -> list[_Stretch]:
        """Los tramos quietos entre las cotas de un hueco ruidoso y sus ráfagas.

        Solo si el transitorio más cercano a la cota es una ráfaga: un
        transitorio corto puede ser un latido atenuado (MIT-BIH 116 y 208), y lo
        quieto antes de él, latidos más atenuados todavía.
        """
        edges: list[_Stretch] = []
        low, high = events[0]
        if high - low > longest:
            kept = tuple((start + a, start + b) for a, b in _runs(measured[:low]))
            if kept:
                edges.append(_Stretch(first, start + low, kept, open_end=True))
        low, high = events[-1]
        if high - low > longest:
            kept = tuple((start + high + a, start + high + b) for a, b in _runs(measured[high:]))
            if kept:
                edges.append(_Stretch(start + high, second, kept, open_start=True))
        return edges

    def _contact_features(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Red y deriva por muestra, y su valor en cada ventana de referencia. Una vez por bloque.

        La red es lo que `deinterfere` le quitó a la señal sin media (cero en
        las muestras inválidas, que dejó crudas). La deriva es la pendiente
        absoluta, en mV/s, de la señal sin media pasada por un pasabajos de
        `DRIFT_LOWPASS_HZ`, con las inválidas puenteadas antes de filtrar: el
        riel de un electrodo despegado no puede contaminar la deriva de las
        ventanas buenas de al lado.
        """
        if self._contact is None:
            evidence = self.evidence
            rate = evidence.sample_rate
            centered = remove_mains(evidence.signal, rate, 0.0).astype(np.float64)
            mains = centered - evidence.report.deinterfered.astype(np.float64)
            invalid = invalid_samples(evidence.signal, evidence.flags)
            bridged = centered.copy()
            valid_positions = np.flatnonzero(~invalid)
            invalid_positions = np.flatnonzero(invalid)
            if valid_positions.size and invalid_positions.size:
                bridged[invalid_positions] = np.interp(
                    invalid_positions, valid_positions, centered[valid_positions]
                )
            sos = butter(2, DRIFT_LOWPASS_HZ, btype="low", fs=float(rate), output="sos")
            # El `padlen` por omisión de `sosfiltfilt`: más corta no se puede filtrar.
            if bridged.size > 3 * (2 * sos.shape[0] + 1):
                slow = sosfiltfilt(sos, bridged)
                drift = np.abs(np.gradient(slow)) * rate
            else:
                drift = np.zeros(bridged.size, dtype=np.float64)
            window_mains = np.array([_rms(mains[a:b]) for a, b in self.reference_windows])
            window_drift = np.array(
                [float(np.percentile(drift[a:b], 95)) for a, b in self.reference_windows]
            )
            self._contact = (mains, drift, window_mains, window_drift)
        return self._contact

    def contact_reference(self, first: int, second: int) -> tuple[float, float] | None:
        """`(red, deriva)` de referencia del hueco, o None.

        Las medianas de las ventanas de referencia con el centro a
        `REFERENCE_SPAN_S` o menos antes de su primer R o después del segundo;
        las que son `bad` solo por la deriva, si no hay otras. Sin ninguna no
        hay contra qué comparar y la regla no informa.
        """
        if not self.reference_windows:
            return None
        centers = self.reference_centers
        near = ((centers >= first - self.span) & (centers <= first)) | (
            (centers >= second) & (centers <= second + self.span)
        )
        if (near & ~self.reference_drifting).any():
            near &= ~self.reference_drifting
        if not near.any():
            return None
        _, _, window_mains, window_drift = self._contact_features()
        return float(np.median(window_mains[near])), float(np.median(window_drift[near]))

    def contact(self, parts: tuple[tuple[int, int], ...]) -> tuple[float, float]:
        """`(red, deriva)` de los pedazos `[inicio, fin)` de un tramo."""
        mains, drift, _, _ = self._contact_features()
        mains_values = np.concatenate([mains[low:high] for low, high in parts])
        drift_values = np.concatenate([drift[low:high] for low, high in parts])
        return _rms(mains_values), float(np.percentile(drift_values, 95))


def _pause(
    first: int, second: int, sample_rate: int, metadata: dict[str, float | int | str]
) -> Finding:
    seconds = (second - first) / sample_rate
    return Finding(
        kind="pause",
        event_type=ECGEventType.PAUSE,
        severity=ECGEventSeverity.CRITICAL
        if seconds >= PAUSE_CRITICAL_SECONDS
        else ECGEventSeverity.HIGH,
        start_sample=first,
        length_samples=second - first,
        dedupe_key=f"pause:{first}",
        score=None,
        beat_count=2,
        alert_message=PAUSE_ALERT,
        beat_samples=(first, second),
        metadata={"pauseSeconds": round(seconds, 3), **metadata},
    )


def _classify(block: _Block) -> tuple[Mask, Mask]:
    """`(acota, corta)` por cada R de NeuroKit, contra su referencia local."""
    ratios = np.full(block.peaks.size, np.nan)
    for position, peak in enumerate(block.peaks.tolist()):
        reference = block.reference(((peak - block.span, peak + block.span),))
        if reference is not None:
            ratios[position] = float(block.amplitude[position]) / reference
    known = ~np.isnan(ratios)
    filled = np.where(known, ratios, 0.0)
    credible = known & (filled >= BOUND_MIN) & (filled <= BOUND_MAX)
    # Sin referencia no se puede decir que es chico: cuenta.
    big_enough = ~known | (filled >= GOOD_BEAT_MIN)
    train = block.train & (~known | ((filled >= TRAIN_MIN) & (filled <= BOUND_MAX)))
    # Un tren de P (bloqueo AV vagal) no es un tren de escape.
    for position in np.flatnonzero(train).tolist():
        peak = int(block.peaks[position])
        p_wave = block.p_wave(((peak - block.span, peak + block.span),))
        if float(block.amplitude[position]) <= P_WAVE_MARGIN * p_wave:
            train[position] = False
    bound = credible | (block.confirmed & big_enough) | train
    cut = bound | (block.good_beat & ~block.arbitrated & big_enough)
    return bound, cut


def _train(
    peaks: np.ndarray, amplitude: np.ndarray, cleaned: np.ndarray, sample_rate: int, size: int
) -> Mask:
    """Si cada R de NeuroKit abre o cierra un tren de escape: `TRAIN_BEATS` R
    seguidos (él y los siguientes, o él y los anteriores) a `TRAIN_MIN_RR_S` o
    más, con R-R y amplitudes parejos (`TRAIN_SPREAD`), y que sobresalen de lo
    que hay entre ellos (`QUIET_MAX` contra el más chico): los R que NeuroKit
    pone sobre el ruido de una asistolia larga también pueden caer parejos,
    pero miden lo mismo que lo que los rodea."""
    train = np.zeros(peaks.size, dtype=bool)
    steps = TRAIN_BEATS - 1
    if peaks.size < TRAIN_BEATS:
        return train
    rr = np.diff(peaks).astype(np.float64)
    shape = np.lib.stride_tricks.sliding_window_view(rr, steps)
    sizes = np.lib.stride_tricks.sliding_window_view(amplitude.astype(np.float64), TRAIN_BEATS)
    smallest = sizes.min(axis=1)
    regular = (
        (shape.min(axis=1) >= TRAIN_MIN_RR_S * sample_rate)
        & (shape.max(axis=1) <= TRAIN_SPREAD * shape.min(axis=1))
        & (smallest > 0)
        & (sizes.max(axis=1) <= TRAIN_SPREAD * np.where(smallest > 0, smallest, 1.0))
    )
    before, after = int(INTERIOR_PRE_S * sample_rate), int(INTERIOR_POST_S * sample_rate)
    for start in np.flatnonzero(regular).tolist():
        loudest = 0.0
        for position in range(start, start + steps):
            low, high = int(peaks[position]) + before, int(peaks[position + 1]) - after
            if high > low:
                loudest = max(loudest, float(np.max(_envelope(cleaned[low:high], size))))
        if loudest <= QUIET_MAX * float(smallest[start]):
            train[start] = True
            train[start + steps] = True
    return train


def _quiet_gap_pauses(block: _Block, *, pause_seconds: float) -> list[Finding]:
    """Las pausas por hueco quieto del bloque, en coordenadas relativas al lote."""
    evidence = block.evidence
    peaks = block.peaks
    if peaks.size == 0:
        return []
    bound, cut = _classify(block)
    cuts = np.flatnonzero(cut).tolist()

    # Los pares de R consecutivos que cortan; si la lectura empieza en medio de
    # su corrida, el tramo abierto hasta el primero (`None` = sin R que abra)
    # cuando ese R es de la parte nueva o del contexto derecho, y el que va del
    # último hasta el final de la lectura (`None` = sin R que cierre). Un R del
    # contexto izquierdo es de la parte nueva del bloque anterior, que lo acotó
    # con su contexto derecho. Uno posterior lo pudo haber leído también el
    # bloque anterior —sobre el final de su lectura, donde no siempre lo puede
    # acotar: su referencia local es el hueco y el `FLAG_R_PEAK` cae 250 ms
    # después, fuera—; si lo informó, la persistencia empalma los dos.
    candidates: list[tuple[int | None, int | None]] = list(zip(cuts[:-1], cuts[1:], strict=True))
    if evidence.context_samples > 0 and cuts and int(peaks[cuts[0]]) >= evidence.context_samples:
        candidates.insert(0, (None, cuts[0]))
    if cuts:
        candidates.append((cuts[-1], None))

    found: list[Finding] = []
    for index_first, index_second in candidates:
        found.extend(_candidate_pauses(block, index_first, index_second, bound, cut, pause_seconds))
    return found


def _candidate_pauses(
    block: _Block,
    index_first: int | None,
    index_second: int | None,
    bound: Mask,
    cut: Mask,
    pause_seconds: float,
) -> list[Finding]:
    """Las pausas de un hueco entre dos R que cortan (o un R y un borde de la lectura)."""
    rate = block.evidence.sample_rate
    peaks = block.peaks
    minimum = pause_seconds * rate
    settle = int(OPEN_SETTLE_S * rate)
    if any(index is not None and not bound[index] for index in (index_first, index_second)):
        return []
    first = 0 if index_first is None else int(peaks[index_first])
    second = block.n - settle if index_second is None else int(peaks[index_second])
    if second - first <= minimum:
        return []
    start, end = block.interior(first, second)
    if index_first is None:
        start = settle
    if index_second is None:
        end = second
    if end <= start:
        return []
    # Todo contra la referencia del hueco: las cotas, el interior y el contacto.
    references = block.gap_reference(first, second)
    contact_reference = block.contact_reference(first, second)
    if references is None or contact_reference is None:
        return []
    reference = references.amplitude
    sides = [index for index in (index_first, index_second) if index is not None]
    if not all(block.bounds(index, references) for index in sides):
        return []
    # Una cota por debajo de `BOUND_MIN` (la confirmó el firmware, o abre un
    # tren de escape) es un escape, no un latido del paciente atenuado: si es
    # de transición, no tiene su forma (`ATTENUATED_SHAPE`), y sobresale de lo
    # quieto que cierra (`_stretch_pause`).
    small = {
        int(peaks[index]): float(block.amplitude[index])
        for index in sides
        if float(block.amplitude[index]) < BOUND_MIN * reference
    }
    transitional = {
        int(peaks[index]): block.transitional(index, step, cut, int(minimum))
        for index, step in ((index_first, -1), (index_second, 1))
        if index is not None and int(peaks[index]) in small
    }
    if any(
        transitional[peak] and block.likeness(peak, first, second) >= ATTENUATED_SHAPE
        for peak in small
    ):
        return []
    # Una extrasístole temprana (`EARLY_FROM_S`): la pausa corre desde ella, y
    # si no es una cota creíble, es una chica (y no de transición: el R que
    # abre está a menos de medio segundo).
    if index_first is not None:
        early = block.early_beat(first, second, references)
        if early is not None:
            first = early
            start = first + int(INTERIOR_PRE_S * rate)
            if second - first <= minimum or end <= start:
                return []
            size = block.early_bound(early, reference)
            if size is not None:
                small[early] = size
                transitional[early] = False
    stretches, edges = block.stretches(first, second, start, end, references)
    if index_first is None:
        # Sin R que abra, lo que arranca en el principio de la lectura es un borde.
        stretches = [
            replace(stretch, open_start=True) if stretch.first == first else stretch
            for stretch in stretches or []
        ] or None
        edges = [edge for edge in edges if edge.first != first]
    if index_second is None:
        # Sin R que cierre, lo que llega al final de la lectura es un borde.
        stretches = [
            replace(stretch, open_end=True) if stretch.second == second else stretch
            for stretch in stretches or []
        ] or None
        edges = [edge for edge in edges if edge.second != second]
    weak = _WeakBounds(small, transitional)
    found: list[Finding] = []
    # Un hueco quieto entero, con la señal que falta mirada en todo él.
    if stretches and not block.missing(first, second, start, end):
        for stretch in stretches:
            if stretch.open_end and stretch.second - stretch.first < OPEN_END_MIN_S * rate:
                continue
            found.extend(_reported(block, stretch, minimum, references, contact_reference, weak))
    # Los bordes de un hueco ruidoso, con la señal que falta mirada solo en ellos.
    for edge in edges:
        low, high = edge.parts[0][0], edge.parts[-1][1]
        if edge.second - edge.first < max(minimum, NOISE_EDGE_MIN_S * rate):
            continue
        if not block.missing(edge.first, edge.second, low, high, edge=True):
            found.extend(_reported(block, edge, minimum, references, contact_reference, weak))
    return found


@dataclass(frozen=True, slots=True)
class _WeakBounds:
    """Las cotas de un hueco por debajo de `BOUND_MIN`: su amplitud, y si son de
    transición (`_Block.transitional`), por muestra de su R."""

    size: dict[int, float]
    transitional: dict[int, bool]


def _reported(
    block: _Block,
    stretch: _Stretch,
    minimum: float,
    references: _Reference,
    contact_reference: tuple[float, float],
    weak: _WeakBounds,
) -> list[Finding]:
    if stretch.second - stretch.first <= minimum:
        return []
    edges = [edge for edge in (stretch.first, stretch.second) if edge in weak.size]
    pause = _stretch_pause(
        block,
        stretch,
        references,
        contact_reference,
        weakest=min((weak.size[edge] for edge in edges), default=None),
        tolerate_p=not any(weak.transitional.get(edge, False) for edge in edges),
    )
    return [] if pause is None else [pause]


def _stretch_pause(
    block: _Block,
    stretch: _Stretch,
    references: _Reference,
    contact_reference: tuple[float, float],
    *,
    weakest: float | None = None,
    tolerate_p: bool = False,
) -> Finding | None:
    """La pausa de un tramo quieto, o None si el contacto cambió en él o si su
    cota más chica (`weakest`, una por debajo de `BOUND_MIN`) no sobresale de
    lo quieto (`QUIET_MAX` contra ella). Con `tolerate_p` (ninguna cota es de
    transición) lo que pasa de eso puede ser P del paciente (`_Block.only_p_waves`)."""
    reference = references.amplitude
    quiet = max(
        float(np.max(_envelope(block.cleaned[low:high], block.envelope)))
        for low, high in stretch.parts
    )
    if (
        weakest is not None
        and quiet > QUIET_MAX_UNDER_WEAK_BOUND * weakest
        and not (
            tolerate_p
            and block.only_p_waves(
                stretch.parts,
                QUIET_MAX_UNDER_WEAK_BOUND * weakest,
                P_WAVE_MARGIN * references.p_seen,
            )
        )
    ):
        return None
    mains_reference, drift_reference = contact_reference
    mains, drift = block.contact(stretch.parts)
    if mains > MAINS_MAX * mains_reference + MAINS_SLACK_MV:
        return None
    if drift > DRIFT_MAX * drift_reference + DRIFT_SLACK_MV_S:
        return None
    metadata: dict[str, float | int | str] = {
        "quietGap": True,
        "interiorRatio": round(quiet / reference, 3),
    }
    # Un lado sin R —el principio o el final de la lectura, una ráfaga de
    # ruido— no tiene cota que medir: lo que se sabe es que hasta ahí, o desde
    # ahí, no hubo latidos.
    if stretch.open_start:
        metadata["openStart"] = True
    else:
        metadata["firstBeatRatio"] = round(_anchor_amplitude(block, stretch.first) / reference, 3)
    if stretch.open_end:
        metadata["openEnd"] = True
    else:
        metadata["lastBeatRatio"] = round(_anchor_amplitude(block, stretch.second) / reference, 3)
    if stretch.events:
        metadata["interiorEvents"] = stretch.events
    if mains_reference > 0:
        metadata["mainsRatio"] = round(mains / mains_reference, 3)
    if drift_reference > 0:
        metadata["driftRatio"] = round(drift / drift_reference, 3)
    return _pause(stretch.first, stretch.second, block.evidence.sample_rate, metadata)


def _anchor_amplitude(block: _Block, sample: int) -> float:
    """Pico a pico de ±`BEAT_HALF_WIDTH_MS` alrededor de una cota."""
    half = block.beat_width // 2
    segment = block.cleaned[max(sample - half, 0) : sample + half + 1]
    return float(segment.max() - segment.min()) if segment.size else 0.0


def _within(inner: Finding, outer: Finding) -> bool:
    return (
        outer.start_sample <= inner.start_sample
        and inner.start_sample + inner.length_samples <= outer.start_sample + outer.length_samples
    )


def refine_pauses(
    findings: list[Finding], evidence: GapEvidence, *, pause_seconds: float
) -> list[Finding]:
    """Los hallazgos de ritmo con las pausas depuradas y las de hueco quieto agregadas.

    Tres cosas, antes de la refractariedad:

    1. Las pausas de hueco quieto (`_quiet_gap_pauses`).
    2. Una pausa del motor que cae adentro de una de hueco quieto más larga se
       reemplaza: es la misma asistolia partida por un pico falso de NeuroKit,
       con la duración —y quizás la severidad— mal.
    3. Una pausa del motor con un latido adentro (`_Block.beat_inside`) se
       descarta, y una con una extrasístole temprana (`_Block.early_beat`)
       corre desde ella —o se descarta, si ya no es una pausa—. Sin referencia
       contra la cual medir, queda como estaba.

    Y una de hueco quieto que cae adentro de una pausa del motor que quedó
    —la misma, o un tramo de ella— no se agrega: el motor ya la vio con un
    R-R válido.
    """
    block = _Block(evidence)
    quiet = _quiet_gap_pauses(block, pause_seconds=pause_seconds)
    engine: list[Finding] = []
    for finding in findings:
        if finding.kind != "pause":
            continue
        if any(_within(finding, item) and not _within(item, finding) for item in quiet):
            continue
        first, second = finding.start_sample, finding.start_sample + finding.length_samples
        # Un R-R válido que cruza un riel no mide el corazón: mide lo que duró el riel.
        if block.marked(block.rails, first, second + 1) or block.beat_inside(first, second):
            continue
        # Una extrasístole temprana que NeuroKit no vio (`EARLY_FROM_S`): la
        # pausa corre desde ella, si todavía lo es.
        references = block.gap_reference(first, second)
        early = None if references is None else block.early_beat(first, second, references)
        if early is not None:
            if second - early > pause_seconds * evidence.sample_rate:
                engine.append(_starting_at(finding, early, evidence.sample_rate))
            continue
        engine.append(finding)
    kept = [finding for finding in findings if finding.kind != "pause"]
    kept.extend(engine)
    kept.extend(item for item in quiet if not any(_within(item, finding) for finding in engine))
    kept.sort(key=lambda item: item.start_sample)
    return kept


def _starting_at(finding: Finding, start: int, sample_rate: int) -> Finding:
    """La pausa del motor `finding`, desde `start` hasta el mismo R que la cierra."""
    second = finding.start_sample + finding.length_samples
    seconds = (second - start) / sample_rate
    return replace(
        finding,
        severity=ECGEventSeverity.CRITICAL
        if seconds >= PAUSE_CRITICAL_SECONDS
        else ECGEventSeverity.HIGH,
        start_sample=start,
        length_samples=second - start,
        dedupe_key=f"pause:{start}",
        beat_samples=(start, second),
        metadata={**finding.metadata, "pauseSeconds": round(seconds, 3)},
    )


def explained_by_pauses(
    windows: tuple[QualityWindow, ...], findings: list[Finding]
) -> tuple[QualityWindow, ...]:
    """Las ventanas de ruido que son, en realidad, una asistolia confirmada.

    Una ventana de 10 s sin QRS sale `bad`/pSQI (o kSQI, basSQI, `no_beats`), y
    `pipeline._quality_findings` la pintaba como `noise_burst` —"los electrodos
    estaban bien y aun así no se pudo leer"— encima de la pausa CRITICAL que la
    explica. Son las que tienen al menos la mitad adentro de una pausa de hueco
    quieto. Solo se dejan de **pintar**: la ventana, la máscara de analizable,
    los intervalos de calidad y los totales no cambian.
    """
    spans = [
        (finding.start_sample, finding.start_sample + finding.length_samples)
        for finding in findings
        if finding.kind == "pause" and finding.metadata.get("quietGap")
    ]
    if not spans:
        return ()
    explained: list[QualityWindow] = []
    for window in windows:
        if window.level is not SignalQualityLevel.BAD or window.reason not in _NOISE_REASONS:
            continue
        low, high = window.start_sample, window.start_sample + window.length_samples
        inside = sum(max(0, min(high, end) - max(low, start)) for start, end in spans)
        if 2 * inside >= window.length_samples:
            explained.append(window)
    return tuple(explained)
