"""Contratos de las métricas Holter que se usan sin cargar numpy.

`studies_service` los necesita para armar la entrada de `compute_holter_metrics`
(qué eventos y qué veredictos del motor excluyen señal, cuáles cortan un RR,
los tramos de la línea de tiempo), y `studies_service` se importa al arrancar la
API. `holter_metrics` carga numpy y scipy a nivel de módulo, y el arranque no los
puede pagar (`test_starting_the_api_does_not_import_numpy`). Por eso viven acá,
sin numpy, y `holter_metrics` los reexporta: quien ya los importaba de ahí no
cambia.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tramos cuyos latidos no se cuentan (reglas de `INTEGRACION.md` §4.5).
EXCLUSION_KINDS = frozenset({"lead_off", "sqi_unanalyzable", "adc_saturated"})
#: Eventos que cortan la continuidad: ningún RR cruza uno. `frame_gap` es
#: adquisición perdida entre dos tramas de `seq` contiguo (`derive_events`): el
#: buffer empaquetado pega las dos tramas, y un RR que cruzara el empalme mediría
#: el hueco y no el corazón —una pausa que nadie tuvo—.
BREAK_KINDS = frozenset(
    {"internal_gap", "backlog_overflow", "missing_frames_inferred", "corrupt_frame", "frame_gap"}
)
#: Veredictos del motor de detección (`signal_quality_interval`, nivel `bad`)
#: que sacan señal de las métricas: los índices espectrales y estadísticos
#: (pSQI, kSQI, basSQI) dicen que en esa ventana hay ruido o deriva y no un
#: ECG, y ahí Pan-Tompkins inventa latidos y extremos de FC.
#:
#: **Salvo para las pausas.** Esos índices son cocientes de potencia y no ven
#: la amplitud: diez segundos sin QRS son el piso de ruido de banda ancha, y el
#: gate marca una asistolia `bad`/pSQI (o basSQI, con deriva) antes de llegar a
#: preguntar si hubo latidos. Medido de punta a punta: excluida como ruido, una
#: asistolia de 12-26 s desaparecía del informe. Así que las pausas se buscan
#: solo contra `EXCLUSION_KINDS` y los cortes (`compute_holter_metrics`), y el
#: médico verifica cada una en su tira.
#:
#: Quedan afuera a propósito:
#:
#: - `no_beats`: la ventana pasó los índices y ningún detector vio un latido.
#: - `flatline`: no es ruido sino señal que falta, y sale de todo —pausas
#:   incluidas— por `HARDWARE_QUALITY_REASONS`.
#: - `marginal` y `bsqi`: sirven para contar latidos y medir RR; sacarlos
#:   achicaría el tiempo analizado con electrodos secos sin ganar nada.
#: - `spectral`: las filas de antes de separar los índices, que además son de
#:   antes de quitar la red eléctrica y se marcaban de más. Nunca se desplegaron.
QUALITY_EXCLUSION_REASONS = frozenset({"psqi", "ksqi", "bassqi"})
#: Veredictos del motor (`bad`) que valen como un tramo del hardware: salen de
#: la FC, la VFC, la cuenta de latidos **y de las pausas**, como un `lead_off`.
#:
#: `flatline` es un riel: 10 s con menos de `ml_flatline_uv` pico a pico
#: (percentiles 5-95) sobre la señal **cruda**, red incluida, o con huecos. La
#: desconexión ya la marca la Capa A (`lead_off`, por `EXCLUSION_KINDS`), pero
#: el riel también llega sin `LEAD_OFF`: un segmento viejo sin flags
#: archivados, un ADC congelado, electrodos en corto. El motor lo declara señal
#: que falta y `quiet_gap` no infiere una pausa a través de él (no le avisa al
#: paciente); si el informe lo buscara solo contra el hardware, el médico
#: tendría en `/holter-metrics` una "pausa" de lo que dure el riel que el motor
#: nunca afirmó. Una asistolia de verdad no es un riel: entre los dos R queda
#: la línea de base con su red y su ruido de electrodo, y el gate la marca por
#: pSQI o basSQI (`QUALITY_EXCLUSION_REASONS`), que sigue sin tocar las pausas.
#: Medido contra el umbral de 20 µV: en el chaleco, entre latidos (segmentos
#: TP de 3420 R-R en 15 capturas) la señal cruda tiene 80 µV como mínimo
#: (percentil 1: 158 µV) y ninguna de sus 377 ventanas sin veto del hardware
#: es `flatline`; en MIT-BIH, el interior de las 85 pausas anotadas tiene
#: 45 µV como mínimo, y 33 µV en su segundo más quieto.
#:
#: Lo que no cubre: un riel de menos de ~20 s puede no llenar ninguna ventana
#: de la grilla de 10 s y no dejar fila `flatline`. Ahí el motor tampoco lo ve
#: como riel, así que los dos siguen alineados: los dos ven una pausa.
#:
#: Se aplica con el resto del veredicto del motor, solo si evaluó toda la señal
#: (`studies_service._metric_noise`): sin él las métricas son las del algoritmo
#: 1 enteras.
HARDWARE_QUALITY_REASONS = frozenset({"flatline"})
ECTOPY_UNAVAILABLE = "BEAT_CLASSIFICATION_UNAVAILABLE"


@dataclass(frozen=True)
class TimelineRun:
    """Un tramo continuo del buffer empaquetado con su hora de pared."""

    start_sample: int
    sample_count: int
    start_epoch_ms: int
    end_epoch_ms: int
