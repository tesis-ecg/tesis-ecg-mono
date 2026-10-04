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
#: - `flatline`: la línea plana por desconexión ya la marca la Capa A
#:   (`lead_off`) y entra por `EXCLUSION_KINDS`. **Salvo** en un segmento
#:   viejo sin flags archivados (o un ADC congelado, o electrodos en corto):
#:   el riel llega sin `LEAD_OFF`, la ventana es `flatline` y el informe busca
#:   pausas a través de ella. Ahí el informe y el motor no coinciden a
#:   propósito: el motor (`quiet_gap`) no infiere una pausa sobre `flatline` y
#:   no le avisa al paciente; el informe la lista y el médico la verifica en su
#:   tira, que muestra el riel. Alinearlos es pasar las ventanas `flatline` como
#:   tramos de hardware desde `studies_service`.
#: - `marginal` y `bsqi`: sirven para contar latidos y medir RR; sacarlos
#:   achicaría el tiempo analizado con electrodos secos sin ganar nada.
#: - `spectral`: las filas de antes de separar los índices, que además son de
#:   antes de quitar la red eléctrica y se marcaban de más. Nunca se desplegaron.
QUALITY_EXCLUSION_REASONS = frozenset({"psqi", "ksqi", "bassqi"})
ECTOPY_UNAVAILABLE = "BEAT_CLASSIFICATION_UNAVAILABLE"


@dataclass(frozen=True)
class TimelineRun:
    """Un tramo continuo del buffer empaquetado con su hora de pared."""

    start_sample: int
    sample_count: int
    start_epoch_ms: int
    end_epoch_ms: int
