"""Orquestación del motor de detección. La única entrada desde `processing.py`.

Todo lo de acá es puro: numpy adentro, dataclasses afuera, sin base de datos ni
S3 ni `async`. Esa frontera es lo que permite (a) testear el pipeline entero con
una señal sintética de tres líneas y (b) moverlo a un hilo con **una sola**
llamada, que es lo que hace falta porque analizar un lote de 1 h cuesta ~4-5 s de
CPU y bloquear el event loop de FastAPI ese tiempo congela la API entera.

El orden no es arbitrario, es la estrategia:

    limpiar → R-peaks → GATE DE CALIDAD → ritmo → morfología → episodios

El gate va **antes** que todo lo clínico. Un artefacto de movimiento se parece
muchísimo más a una arritmia que a un latido normal, así que un motor que no
descarta ruido primero produce cientos de falsos positivos por día. Lo único
que el ritmo mira detrás del gate es la asistolia larga, cuyas ventanas sin QRS
el gate rechaza: `quiet_gap.py` la informa solo si el hueco está quieto contra
los latidos del propio paciente y no falta señal.

Al final, y solo si `ml_interval_measurements_enabled`, se miden QT, QTc y
amplitud R del bloque (`app/ml/intervals.py`). Es dato de investigación: no
produce hallazgos, no entra en los totales y ninguna API lo lee.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import structlog

from app.core.config import Settings
from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityLevel
from app.ml import morphology
from app.ml.arrhythmia import detect_rhythm
from app.ml.contracts import (
    EpisodeBudget,
    Finding,
    Flags,
    Indices,
    Mask,
    QualityReport,
    QualityThresholds,
    QualityWindow,
    RhythmThresholds,
    Signal,
)
from app.ml.episodes import RECURRENT_KIND, apply_refractory, enforce_budget, group_beats
from app.ml.hrv import build_rr, expected_rr, prematurity
from app.ml.intervals import IntervalMeasurement, IntervalThresholds, measure_intervals
from app.ml.morphology import BeatAssignment, BeatMatrix, TemplateBank
from app.ml.quality import (
    assess_quality,
    exclude_splices,
    invalid_samples,
    merge_windows,
    remove_mains,
    window_counts,
)
from app.ml.quiet_gap import GapEvidence, explained_by_pauses, refine_pauses
from app.ml.rpeak_detection import (
    clean_signal,
    compensate_firmware_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)
from app.ml.totals import block_totals, summary_from_totals

logger = structlog.get_logger(__name__)

#: Versión del pipeline. Viaja en `ecg_event.model_version` y se **pinnea por
#: estudio** en el primer lote analizado: si el código sube de versión a mitad de
#: un registro, el estudio sigue con el banco viejo. Cambiar de versión a mitad
#: renumeraría clusters y haría mentir a los eventos ya escritos.
PIPELINE_VERSION = "ml-1.0.0"

#: Motivos de la Capa B que sí producen un `ecg_event`. Los de la Capa A
#: (`lead_off`, `saturated`, `firmware_sqi`) los emite `derive_events` desde los
#: bits del hardware y duplicarlos llenaría la traza de bandas repetidas.
_QUALITY_EVENT_KINDS = {
    "flatline": ("flatline", ECGEventSeverity.MEDIUM),
    "psqi": ("noise_burst", ECGEventSeverity.LOW),
    "ksqi": ("noise_burst", ECGEventSeverity.LOW),
    "bassqi": ("noise_burst", ECGEventSeverity.LOW),
    "bsqi": ("noise_burst", ECGEventSeverity.LOW),
    "no_beats": ("noise_burst", ECGEventSeverity.LOW),
}


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    sample_rate: int
    quality: QualityThresholds
    rhythm: RhythmThresholds
    match_threshold: float
    merge_threshold: float
    max_templates: int
    recurrent_min_beats: int
    #: Fracción mínima de los miembros que pudieron puntuar que tiene que haber
    #: puntuado para que una plantilla que no es la dominante tenga encabezado
    #: de foco recurrente (`morphology.is_recurrent`). No filtra episodios.
    recurrent_min_anomalous_fraction: float
    anomaly_score_min: float
    budget: EpisodeBudget
    #: Umbrales de la medición de intervalos. `None` la apaga: no se filtra ni
    #: se delinea nada (`ml_interval_measurements_enabled`).
    intervals: IntervalThresholds | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Salida del análisis de un bloque, con coordenadas **absolutas al estudio**.

    `totals` son las sumas de la parte nueva (`totals.block_totals`): se
    acumulan en el estudio con `totals.combine_totals`. `metrics` es su resumen
    legible más el estado del banco, y existe para los lectores de
    `ml_state["metrics"]`; describe **este** bloque, no el estudio.

    `intervals` son las medianas de QT, QTc y amplitud R de los latidos de la
    parte nueva (`_measure_intervals`), o None: medición apagada, menos de
    `min_beats` latidos válidos, o un error que no puede tumbar el bloque. Es
    dato de investigación y se persiste aparte (`ecg_interval_measurement`).
    """

    quality_intervals: tuple[tuple[QualityWindow, int], ...]
    findings: tuple[Finding, ...]
    bank: TemplateBank
    metrics: dict[str, float]
    model_version: str
    totals: dict[str, float] = field(default_factory=dict)
    intervals: IntervalMeasurement | None = None
    #: Plantillas cuyo encabezado de foco hay que dar de baja: pasaron a ser la
    #: dominante, el latido del paciente (`morphology.hand_over_dominance`).
    retracted_headers: tuple[int, ...] = ()


def build_config(
    settings: Settings, sample_rate: int, *, score_floor: float = 0.0
) -> PipelineConfig:
    """Traduce los settings a los umbrales del motor.

    Es el único lugar donde se convierten unidades: los settings hablan en
    segundos, µV y latidos porque es lo que un cardiólogo puede revisar; el motor
    trabaja en muestras.
    """
    return PipelineConfig(
        sample_rate=sample_rate,
        quality=QualityThresholds(
            window_samples=max(int(settings.ml_quality_window_seconds * sample_rate), 1),
            flatline_mv=settings.ml_flatline_uv / 1000.0,
            psqi_min=settings.ml_quality_psqi_min,
            ksqi_min=settings.ml_quality_ksqi_min,
            bassqi_min=settings.ml_quality_bassqi_min,
            bsqi_min=settings.ml_quality_bsqi_min,
            # ±150 ms: más que el jitter del detector del MCU alrededor de su
            # retardo nominal, menos que el R-R más corto plausible. El retardo
            # en sí no entra acá: se resta antes de comparar.
            bsqi_tolerance_samples=max(int(0.150 * sample_rate), 1),
            firmware_lag_samples=int(settings.ml_firmware_peak_lag_ms * sample_rate / 1000.0),
            firmware_refractory_samples=int(
                settings.ml_firmware_peak_refractory_ms * sample_rate / 1000.0
            ),
            mains_hz=settings.ml_mains_hz,
        ),
        rhythm=RhythmThresholds(
            tachycardia_bpm=settings.ml_tachycardia_bpm,
            bradycardia_bpm=settings.ml_bradycardia_bpm,
            pause_seconds=settings.ml_pause_seconds,
            min_duration_seconds=settings.ml_rhythm_min_seconds,
        ),
        match_threshold=settings.ml_template_match_threshold,
        merge_threshold=settings.ml_template_merge_threshold,
        max_templates=settings.ml_template_max,
        recurrent_min_beats=settings.ml_recurrent_cluster_min_beats,
        recurrent_min_anomalous_fraction=settings.ml_recurrent_cluster_min_anomalous_fraction,
        anomaly_score_min=settings.ml_anomaly_score_min,
        budget=EpisodeBudget(
            refractory_seconds=settings.ml_episode_refractory_seconds,
            gap_beats=settings.ml_episode_gap_beats,
            min_beats=settings.ml_episode_min_beats,
            max_per_study=settings.ml_findings_max_per_study,
            max_per_kind=settings.ml_findings_max_per_kind,
            score_floor=score_floor,
        ),
        # Los umbrales por defecto son los que se validaron contra la QT
        # Database (`tools/physionet/README.md`): no se exponen como settings
        # porque moverlos invalida esa evidencia.
        intervals=IntervalThresholds() if settings.ml_interval_measurements_enabled else None,
    )


def empty_bank(config: PipelineConfig) -> TemplateBank:
    return TemplateBank(
        model_version=PIPELINE_VERSION, beat_length=morphology.beat_length(config.sample_rate)
    )


def analyze_batch(
    signal_mv: Signal,
    flags: Flags,
    *,
    start_sample_index: int,
    bank: TemplateBank,
    config: PipelineConfig,
    fold_key: str,
    existing_anomalies: int = 0,
    context_samples: int = 0,
    lookahead_samples: int = 0,
    gap_samples: tuple[tuple[int, int], ...] = (),
    flags_known: Mask | None = None,
) -> PipelineResult:
    """Analiza un bloque. **Bloqueante y CPU-bound**: llamar desde un hilo.

    `signal_mv[0]` es la muestra `start_sample_index` del estudio. Las primeras
    `context_samples` son **contexto izquierdo**: señal que ya analizó el
    bloque anterior de la misma corrida y que se vuelve a pasar solo para que
    lo nuevo no arranque en frío. Un lote de ~15 s nunca llega a los 30 s que
    pide una taquicardia, y sin contexto cada borde de bloque corta un R-R, una
    pausa o la referencia de prematuridad de los primeros latidos. Del contexto
    se usa todo lo que sirve para mirar la parte nueva, pero de él no se
    **informa** nada que el bloque anterior ya haya informado:

    - Calidad: las ventanas se cubren por separado a cada lado del borde
      (`quality.block_window_bounds`). Se informan —intervalos, hallazgos,
      totales— solo las de la parte nueva; las del contexto solo arman la
      máscara de analizable con la que se validan los R-R.
    - Ritmo: la serie R-R es la del bloque entero. Se descarta el hallazgo que
      termina antes de la parte nueva (con una franja de guarda, ver
      `_frontier_guard_samples`) y se conserva el que empieza en el contexto y
      termina en ella, **con su inicio en el contexto**: la persistencia lo
      empalma con el evento que ya escribió el bloque anterior.
    - Morfología: solo los latidos de la parte nueva se asignan y se pliegan al
      banco. "De la parte nueva" es que su ventana termina después del borde
      (`morphology.windows_ending_after`): el R puede caer hasta `BEAT_POST_MS`
      antes, porque el bloque anterior no tenía la ventana completa de ese
      latido. Los del contexto aportan su R-R, que es contra lo que se mide la
      prematuridad de los primeros nuevos, y se puntúan contra el banco para
      agrupar episodios a través del borde: se informa el grupo que tiene al
      menos un latido nuevo, aunque arranque en el contexto. Con los nuevos el
      banco acumula también la frecuencia de cada forma, cuántos pudieron
      puntuar y cuántos puntuaron (`morphology.count_anomalous`), y solo cuando
      pliega.
    - Totales: ver `totals.block_totals`.
    - Intervalos (si `config.intervals`): se delinea el bloque entero, pero se
      miden solo los latidos con el R en la parte nueva; ver
      `_measure_intervals`.

    Las últimas `lookahead_samples` son **contexto derecho**: señal que es del
    bloque siguiente y se lee para que el final de la parte nueva no sea un
    borde duro. Sin él, lo que este bloque decidía en sus últimos segundos era
    final y estaba mal medido: el R de los últimos milisegundos se perdía o
    salía uno fantasma sobre el QRS cortado, la mediana de prematuridad de los
    últimos dieciséis latidos repetía el último R-R —un bloque que terminaba en
    un ectópico lo medía contra sí mismo y lo metía en el RMSSD— y la última
    ventana de calidad se juzgaba con el notch sin asentar. Del contexto derecho
    no se informa nada que sea del bloque siguiente: ni ventanas, ni latidos
    que contar o plegar, ni un hallazgo que **empiece** ahí. Uno que empieza en
    la parte nueva y termina en el contexto derecho sí, entero: el bloque
    siguiente lo vuelve a ver desde su contexto izquierdo y la persistencia lo
    empalma.

    `gap_samples` son los empalmes de la corrida (`frame_gap`, `internal_gap`)
    relativos a `signal_mv[0]`: adquisición perdida que no tiene muestras en el
    buffer. Se excluyen de la máscara de analizable (`quality.exclude_splices`).

    `flags_known` marca las muestras cuyos flags se archivaron; `None` es que
    todas. Las que no —un estudio ingerido antes de que cada lote archivara sus
    flags— llegan en cero, y el gate no les pide bSQI (`quality.assess_quality`).

    `fold_key` es una red de seguridad: si coincide con la del último tramo
    plegado al banco, los latidos se puntúan contra el banco pero no se vuelven
    a sumar —contarlos dos veces falsearía la carga (`burdenPct`) que lee el
    médico—. Con el cursor de bloques es la clave del bloque, así que un bloque
    que se reintenta no se pliega dos veces.
    """
    sample_rate = config.sample_rate
    n_samples = int(signal_mv.size)
    context = min(max(int(context_samples), 0), n_samples)
    #: Final de la parte nueva: de acá en adelante es contexto derecho.
    end = max(n_samples - max(int(lookahead_samples), 0), context)
    cleaned = clean_signal(signal_mv, sample_rate)
    firmware_peaks = compensate_firmware_peaks(
        firmware_rpeaks(flags),
        lag_samples=config.quality.firmware_lag_samples,
        refractory_samples=config.quality.firmware_refractory_samples,
    )
    detected_peaks = detect_rpeaks(cleaned, sample_rate)

    report = assess_quality(
        signal_mv,
        flags,
        cleaned,
        firmware_peaks,
        detected_peaks,
        sample_rate=sample_rate,
        thresholds=config.quality,
        context_samples=context,
        lookahead_samples=n_samples - end,
        flags_known=flags_known,
    )
    reported = tuple(window for window in report.windows if context <= window.start_sample < end)
    analyzable = exclude_splices(report.analyzable, gap_samples, sample_rate)

    rr = build_rr(detected_peaks, analyzable, sample_rate)
    # Las pausas que `detect_rhythm` no puede ver, y la depuración de las que
    # sí (`quiet_gap.refine_pauses`): una asistolia larga deja ventanas enteras
    # sin QRS, el gate las rechaza y el R-R que la cruza queda inválido. Corre
    # sobre el bloque entero, contextos incluidos, igual que la serie R-R.
    rhythm_findings = refine_pauses(
        detect_rhythm(rr, config.rhythm, sample_rate),
        GapEvidence(
            signal=signal_mv,
            flags=flags,
            cleaned=cleaned,
            rpeaks=detected_peaks,
            firmware_peaks=firmware_peaks,
            report=report,
            analyzable=analyzable,
            splice_free=exclude_splices(np.ones(n_samples, dtype=bool), gap_samples, sample_rate),
            sample_rate=sample_rate,
            tolerance_samples=config.quality.bsqi_tolerance_samples,
            context_samples=context,
            lookahead_samples=n_samples - end,
            flatline_mv=config.quality.flatline_mv,
        ),
        pause_seconds=config.rhythm.pause_seconds,
    )
    # La refractariedad corre ANTES de descartar lo del contexto: una pausa del
    # contexto y otra a 5 s, ya en la parte nueva, son un solo hallazgo cuando
    # el lote es uno solo, y tienen que seguir siéndolo cuando el borde cae
    # entre las dos. Fundidas, empiezan en el contexto y la persistencia las
    # empalma con el evento de la primera.
    #
    # La refractariedad se aplica SOLO a los hallazgos que pueden disparar un
    # aviso al paciente. Sobre morfología produciría bandas fantasma: dos
    # ectópicos separados por 12 s se fusionaban en un hallazgo de 12,5 s con dos
    # latidos adentro, y el visor lo pinta como una banda continua sobre señal
    # que en su enorme mayoría es normal. Para morfología la agregación correcta
    # ya existe y es otra: `gap_beats` dentro del episodio y el hallazgo de
    # estudio por cluster, que cuenta ocurrencias sin mentir sobre la extensión.
    #
    # Los de calidad tampoco pasan: no avisan a nadie y ya salen agrupados por
    # contigüidad exacta. Con la refractariedad de 10 s, dos tramos malos
    # separados por UNA ventana buena (hueco de 10 s justos) se volvían a fundir
    # en una banda de ruido pintada encima de la señal buena del medio.
    rhythm = apply_refractory(
        rhythm_findings,
        sample_rate=sample_rate,
        refractory_seconds=config.budget.refractory_seconds,
    )
    # El final de un hallazgo de ritmo es la posición de su último R, una
    # muestra que el hallazgo incluye. El bloque anterior vio hasta la muestra
    # `context - 1`, pero **no todos sus R**: el detector no encuentra un R cuyo
    # QRS quedó cortado por el final de la señal (medido: los de los últimos
    # 30-50 ms se pierden). Una pausa que cerraba con ese R no la informó nadie
    # —el bloque anterior no vio el R que la cierra y este la descartaba por
    # terminar en su contexto—. Por eso se descarta solo lo que termina antes de
    # `context - guard`: lo que cae en esa franja se vuelve a informar, y si el
    # bloque anterior ya lo había escrito la persistencia lo empalma con su
    # evento sin volver a avisar.
    #
    # Lo que empieza en el contexto derecho es del bloque siguiente, que lo ve
    # entero y con su propio contexto.
    guard = _frontier_guard_samples(sample_rate) if context else 0
    findings: list[Finding] = [
        item
        for item in rhythm
        if item.start_sample + item.length_samples >= context - guard and item.start_sample < end
    ]
    # Las ventanas de una asistolia confirmada no se pintan como ruido: la pausa
    # las explica (`quiet_gap.explained_by_pauses`). Siguen siendo `bad` en los
    # intervalos de calidad, la máscara y los totales.
    explained = set(explained_by_pauses(reported, rhythm))
    quality_findings = _quality_findings(
        tuple(window for window in reported if window not in explained), sample_rate
    )

    # --- Etapa 2 ------------------------------------------------------------- #
    extracted = morphology.extract_beats(cleaned, detected_peaks, analyzable, sample_rate)
    # Son de este bloque los latidos cuya ventana termina en la parte nueva, no
    # solo los que tienen el R ahí: el bloque anterior terminaba en `context` y
    # no pudo extraer los que tienen el R en sus últimos `BEAT_POST_MS`. La
    # misma regla en el otro borde: los que terminan después de `end` son del
    # bloque siguiente, aunque acá el contexto derecho los deje extraer.
    in_context = ~morphology.windows_ending_after(extracted, context, sample_rate)
    owned = ~in_context & ~morphology.windows_ending_after(extracted, end, sample_rate)
    beats = morphology.select_beats(extracted, owned)
    # El R-R con que se esperaba cada latido —la referencia de la prematuridad,
    # de la misma serie con los contextos— viaja con él al banco (la frecuencia
    # a la que se aprende cada forma) y al score.
    beat_expected_rr = expected_rr(rr)
    # Un banco de antes de los contadores resuelve acá, antes de plegar, qué
    # plantillas ya eran un foco informado (`morphology.mark_reported`).
    bank = _mark_reported(bank, config)
    folds = fold_key != bank.last_fold_key
    if folds:
        updated_bank, assignment = morphology.assign_and_update(
            bank,
            beats,
            match_threshold=config.match_threshold,
            max_templates=config.max_templates,
            fold_key=fold_key,
            sample_offset=start_sample_index,
            expected_rr=beat_expected_rr[beats.beat_index],
        )
    else:
        updated_bank = bank
        assignment = morphology.score_only(bank, beats, match_threshold=config.match_threshold)

    retracted: tuple[int, ...] = ()
    if beats.n_beats:
        beat_prematurity = prematurity(rr)
        # Los latidos del contexto también se puntúan —contra el banco, sin
        # plegarlos: ya los plegó el bloque anterior— para agrupar los episodios
        # **a través del borde**. Agrupando solo los nuevos, un par de ectópicos
        # con un latido a cada lado del borde quedaba en dos grupos de uno, que
        # sin morfología recurrente no llegan al mínimo: el par se perdía. Y un
        # bigeminismo de una hora salía partido en un evento por bloque.
        # Los del contexto derecho no: el bloque siguiente los agrupa con los
        # suyos, puntuados contra un banco que ya plegó los de este.
        previous = morphology.select_beats(extracted, in_context)
        grouped = morphology.concat_beats(previous, beats)
        previous_ids = morphology.score_only(
            updated_bank, previous, match_threshold=config.match_threshold
        ).cluster_ids
        # Contra la plantilla DOMINANTE, no contra la asignada: ver
        # `morphology.dissimilarity_to_dominant`. Y a la frecuencia de cada
        # latido: sin eso, a 155 lpm los ±250 ms traían la T del latido anterior
        # y la propia llegaba antes, y la taquicardia sinusal de una escalera
        # salía entera como morfología atípica.
        grouped_prematurity = beat_prematurity[grouped.beat_index]
        scores = morphology.anomaly_score(
            morphology.dissimilarity_to_dominant(
                updated_bank,
                grouped,
                morphology.BeatRate(
                    expected_rr=beat_expected_rr[grouped.beat_index],
                    prematurity=grouped_prematurity,
                    sample_rate=sample_rate,
                ),
            ),
            grouped_prematurity,
            match_threshold=config.match_threshold,
        )
        positive = scores >= config.anomaly_score_min
        if folds:
            # Cuántos de los latidos que se acaban de plegar pudieron puntuar y
            # cuántos puntuaron, por plantilla: es lo que separa un foco de una
            # variante de la forma normal (`morphology.is_recurrent`). Con la
            # misma regla del pliegue: un tramo ya plegado no vuelve a sumar.
            updated_bank, retracted = morphology.hand_over_dominance(
                bank,
                morphology.count_anomalous(
                    updated_bank, assignment.cluster_ids, positive[previous.n_beats :]
                ),
                sample_rate=sample_rate,
                match_threshold=config.match_threshold,
                anomaly_score_min=config.anomaly_score_min,
            )
            updated_bank = _mark_reported(updated_bank, config)
        # Solo los grupos con al menos un latido nuevo: los que quedan enteros
        # en el contexto ya los informó el bloque anterior. Uno que arranca en
        # el contexto conserva su inicio ahí, y la persistencia lo empalma con
        # el evento que escribió el bloque anterior (mismo foco, se solapan).
        findings.extend(
            group_beats(
                grouped.rpeaks,
                positive,
                scores,
                np.concatenate((previous_ids, assignment.cluster_ids)),
                # Por conteo solo: un supraventricular suelto cae en cualquier
                # variante de la forma normal (`TemplateBank.recurrent_ids`).
                updated_bank.recurrent_ids(config.recurrent_min_beats),
                sample_rate=sample_rate,
                budget=config.budget,
                owned=np.arange(grouped.n_beats) >= previous.n_beats,
            )
        )

    findings, score_floor = enforce_budget(
        findings + quality_findings, budget=config.budget, existing_anomalies=existing_anomalies
    )

    # Los hallazgos de estudio se agregan DESPUÉS del presupuesto: son un
    # encabezado por morfología, no compiten contra los episodios individuales y
    # tienen su propio techo natural (`max_templates`).
    absolute = [_shift(finding, start_sample_index) for finding in findings]
    absolute.extend(_recurrent_findings(updated_bank, config))

    merged = merge_windows(reported)
    intervals = tuple(
        (
            QualityWindow(
                start_sample=interval.start_sample + start_sample_index,
                length_samples=interval.length_samples,
                level=interval.level,
                reason=interval.reason,
                psqi=interval.psqi,
                ksqi=interval.ksqi,
                bassqi=interval.bassqi,
                bsqi=interval.bsqi,
            ),
            window_counts(reported, interval),
        )
        for interval in merged
    )

    totals = block_totals(
        rr,
        reported,
        analyzable,
        context_samples=context,
        n_samples=n_samples,
        sample_rate=sample_rate,
        lookahead_samples=n_samples - end,
    )
    measurement = (
        _measure_intervals(
            signal_mv,
            flags,
            cleaned,
            detected_peaks,
            analyzable,
            extracted=extracted,
            owned_beats=owned,
            assignment=assignment,
            bank=updated_bank,
            context=context,
            end=end,
            config=config,
            thresholds=config.intervals,
        )
        if config.intervals is not None
        else None
    )
    return PipelineResult(
        quality_intervals=intervals,
        findings=tuple(absolute),
        bank=replace(updated_bank, score_floor=score_floor),
        metrics=_metrics(totals, report, reported, beats.n_beats, updated_bank),
        model_version=PIPELINE_VERSION,
        totals=totals,
        intervals=measurement,
        retracted_headers=retracted,
    )


# --------------------------------------------------------------------------- #
# Intervalos (dato de investigación)
# --------------------------------------------------------------------------- #


#: Corte del pasaaltos de la señal para la amplitud R: el mismo de la primera
#: etapa de `nk.ecg_clean` (Butterworth de orden 5, fase cero), sin la segunda,
#: que es el pasabajos que aplana el R. Es la señal con la que se validó la
#: amplitud en QTDB (`highpass` del harness, `tools/physionet/qtdb.py`).
_AMPLITUDE_HIGHPASS_HZ = 0.5
_AMPLITUDE_HIGHPASS_ORDER = 5


def _measure_intervals(
    signal_mv: Signal,
    flags: Flags,
    cleaned: Signal,
    rpeaks: Indices,
    analyzable: Mask,
    *,
    extracted: BeatMatrix,
    owned_beats: Mask,
    assignment: BeatAssignment,
    bank: TemplateBank,
    context: int,
    end: int,
    config: PipelineConfig,
    thresholds: IntervalThresholds,
) -> IntervalMeasurement | None:
    """QT, QTc y amplitud R de los latidos de la parte nueva, o None.

    Se delinea el bloque **entero** —el delineador segmenta cada latido hasta
    la mitad del R-R con sus vecinos—, con `analyzable` como máscara de señal
    buena: las ventanas GOOD de los tres tramos, sin el entorno de los
    empalmes. Pero se miden solo los latidos con el R en la parte nueva
    (`owned` de `intervals.measure_intervals`), así que cada latido del estudio
    entra en la mediana de un solo bloque. El contexto izquierdo aporta el R-R
    previo del primero y el derecho deja terminar la T del último; los
    latidos de ahí no se miden.

    La máscara de morfología dominante sale de la asignación del bloque:
    `assignment` para los latidos que plegó este bloque y el banco ya
    actualizado para los demás que se pudieron extraer (contexto y contexto
    derecho). Un latido que no se pudo extraer —señal no GOOD, ventana
    cortada— no es dominante, y eso lo saca a él y a sus vecinos.

    **No tumba el bloque.** Un error acá —NeuroKit ya está cubierto adentro de
    `intervals`; esto es para un defecto propio— se registra y devuelve None:
    una medición de investigación no puede trabar el cursor del motor, que
    desde la integración con las métricas Holter bloquea la finalización del
    informe mientras no llegue al final del estudio.
    """
    try:
        n_samples = int(cleaned.size)
        new_part = (rpeaks >= context) & (rpeaks < end)
        candidates = new_part & analyzable[np.clip(rpeaks, 0, max(n_samples - 1, 0))]
        # Sin `min_beats` R de la parte nueva sobre señal GOOD no hay mediana
        # posible: ni se filtra la señal para la amplitud ni se delinea.
        if n_samples == 0 or int(np.count_nonzero(candidates)) < thresholds.min_beats:
            return None
        return measure_intervals(
            cleaned,
            _amplitude_signal(signal_mv, flags, config.sample_rate, config.quality.mains_hz),
            rpeaks,
            analyzable,
            config.sample_rate,
            thresholds,
            dominant=_dominant_mask(
                rpeaks.size, extracted, owned_beats, assignment, bank, config.match_threshold
            ),
            owned=new_part,
        )
    except Exception:  # noqa: BLE001 — ver el docstring: nunca tumba el bloque
        logger.warning("ml_interval_measurement_failed", exc_info=True)
        return None


def _dominant_mask(
    n_peaks: int,
    extracted: BeatMatrix,
    owned_beats: Mask,
    assignment: BeatAssignment,
    bank: TemplateBank,
    match_threshold: float,
) -> Mask:
    """Por R del tren: verdadero si el latido es de la plantilla dominante del banco."""
    mask = np.zeros(n_peaks, dtype=bool)
    dominant = morphology.dominant_template(bank)
    if dominant is None:
        return mask
    folded = extracted.beat_index[owned_beats]
    mask[folded] = assignment.cluster_ids == dominant.cluster_id
    others = morphology.select_beats(extracted, ~owned_beats)
    scored = morphology.score_only(bank, others, match_threshold=match_threshold)
    mask[others.beat_index] = scored.cluster_ids == dominant.cluster_id
    return mask


def _amplitude_signal(signal_mv: Signal, flags: Flags, sample_rate: int, mains_hz: float) -> Signal:
    """La señal sin red y sin línea de base, **sin pasabajos**: `raw_for_amplitude`.

    `clean_signal` aplana el R (~19 % en QTDB) y la amplitud se mide sobre
    esto (ver `intervals.measure_intervals`). Las muestras inválidas —riel del
    AFE, no finitas— se puentean con una recta antes de filtrar, como hace
    `quality.deinterfere`: el notch sobre un escalón de cientos de milivoltios
    oscila a 50 Hz durante ~1 s y esa oscilación caería encima de los R de la
    primera ventana buena después de un electrodo despegado. Las inválidas
    nunca son GOOD, así que su valor puenteado no se lee.

    Los dos filtros son de fase cero (`filtfilt` y `sosfiltfilt`): los índices
    de `cleaned` valen acá.
    """
    from scipy import signal as sp_signal

    data = np.asarray(signal_mv, dtype=np.float64)
    invalid = invalid_samples(signal_mv, flags)
    valid_positions = np.flatnonzero(~invalid)
    if valid_positions.size == 0:
        return np.zeros(data.size, dtype=np.float32)
    if valid_positions.size < data.size:
        invalid_positions = np.flatnonzero(invalid)
        data = data.copy()
        data[invalid_positions] = np.interp(
            invalid_positions, valid_positions, data[valid_positions]
        )
    notched = remove_mains(data.astype(np.float32), sample_rate, mains_hz)
    sos = sp_signal.butter(
        _AMPLITUDE_HIGHPASS_ORDER,
        _AMPLITUDE_HIGHPASS_HZ,
        btype="highpass",
        output="sos",
        fs=float(sample_rate),
    )
    return np.asarray(sp_signal.sosfiltfilt(sos, notched.astype(np.float64)), dtype=np.float32)


# --------------------------------------------------------------------------- #
# Traducciones
# --------------------------------------------------------------------------- #


def _frontier_guard_samples(sample_rate: int) -> int:
    """Franja antes del borde donde un hallazgo de ritmo se vuelve a informar.

    `BEAT_POST_MS` (250 ms): holgada contra los 30-50 ms en que el detector
    pierde un R al final de la señal, y del mismo largo que la franja donde el
    bloque anterior no pudo extraer latidos (`morphology.windows_ending_after`).
    """
    return int(round(morphology.BEAT_POST_MS * sample_rate / 1000))


def _shift(finding: Finding, offset: int) -> Finding:
    return Finding(
        kind=finding.kind,
        event_type=finding.event_type,
        severity=finding.severity,
        start_sample=finding.start_sample + offset,
        length_samples=finding.length_samples,
        dedupe_key=f"{finding.kind}:{finding.start_sample + offset}"
        if finding.scope == "batch"
        else finding.dedupe_key,
        score=finding.score,
        scope=finding.scope,
        cluster_id=finding.cluster_id,
        beat_count=finding.beat_count,
        alert_message=finding.alert_message,
        metadata=finding.metadata,
        beat_samples=tuple(sample + offset for sample in finding.beat_samples),
    )


def _quality_findings(windows: tuple[QualityWindow, ...], sample_rate: int) -> list[Finding]:
    """Un evento por tramo que la Capa B rechazó y la Capa A había dejado pasar.

    Solo esos: cuando el electrodo está despegado ya hay un `lead_off` de
    `derive_events` sobre el mismo tramo, y pintar dos bandas encima de la misma
    zona no le dice nada nuevo al médico. Lo que sí es información nueva es
    "los electrodos estaban bien y aun así no se pudo leer".

    Se agrupa por **tipo de evento y contigüidad**, no por motivo: una ventana
    `psqi` seguida de una `ksqi` es el mismo ruido y va en una sola banda (manda
    el motivo de la primera). Y nunca se cruza una ventana que no generó evento:
    dos tramos malos separados por uno bueno son dos bandas, no una que pinta
    de ruido la señal buena del medio.
    """
    findings: list[Finding] = []
    for window in windows:
        mapped = (
            _QUALITY_EVENT_KINDS.get(window.reason)
            if window.level is SignalQualityLevel.BAD
            else None
        )
        if mapped is None:
            continue
        kind, severity = mapped
        last = findings[-1] if findings else None
        if (
            last is not None
            and last.kind == kind
            and last.start_sample + last.length_samples == window.start_sample
        ):
            length = last.length_samples + window.length_samples
            findings[-1] = replace(
                last,
                length_samples=length,
                metadata={**last.metadata, "durationSeconds": round(length / sample_rate, 2)},
            )
            continue
        findings.append(
            Finding(
                kind=kind,
                event_type=ECGEventType.NOISE,
                severity=severity,
                start_sample=window.start_sample,
                length_samples=window.length_samples,
                dedupe_key=f"{kind}:{window.start_sample}",
                metadata={
                    "reason": window.reason,
                    "durationSeconds": round(window.length_samples / sample_rate, 2),
                },
            )
        )
    return findings


def _mark_reported(bank: TemplateBank, config: PipelineConfig) -> TemplateBank:
    return morphology.mark_reported(
        bank,
        min_beats=config.recurrent_min_beats,
        min_anomalous_fraction=config.recurrent_min_anomalous_fraction,
    )


def _recurrent_findings(bank: TemplateBank, config: PipelineConfig) -> list[Finding]:
    """Un hallazgo por morfología recurrente, con el conteo acumulado del estudio.

    Es lo que convierte "412 latidos raros" en "una morfología que aparece 412
    veces, el 0,4 % del registro". Se **upsertea** en cada lote, así que el
    contador crece solo.

    La plantilla **dominante no cuenta**: es el latido normal del paciente. Se
    identifica como la de más miembros, que es lo que es por definición — un
    Holter normal tiene más del 90 % de sus latidos en una sola morfología.

    Tampoco cuenta una variante de la normal: una plantilla cuyos miembros casi
    nunca puntuaron (`morphology.is_recurrent`). Es la que abre una taquicardia
    sinusal de esfuerzo, y sin esa condición se informaba como un foco con la
    carga de toda la taquicardia. Lo que se lee es la marca
    (`Template.reported`, `_mark_reported`) y no la condición de hoy: un foco
    que ya se informó se sigue informando, porque la persistencia solo hace
    upsert y un encabezado que dejara de emitirse quedaría congelado.
    """
    if len(bank.templates) < 2 or bank.beats_seen == 0:
        return []
    dominant = max(bank.templates, key=lambda template: template.count)
    findings: list[Finding] = []
    for template in bank.templates:
        if template.cluster_id == dominant.cluster_id:
            continue
        if not template.reported or template.count < config.recurrent_min_beats:
            continue
        compactness = template.sum_correlation / template.count if template.count else 0.0
        findings.append(
            Finding(
                kind=RECURRENT_KIND,
                event_type=ECGEventType.ANOMALY,
                severity=ECGEventSeverity.MEDIUM,
                start_sample=template.first_sample,
                length_samples=max(template.last_sample - template.first_sample, 1),
                dedupe_key=f"cluster:{template.cluster_id}",
                score=round(min(template.count / max(bank.beats_seen, 1) * 10.0, 1.0), 6),
                scope="study",
                cluster_id=template.cluster_id,
                beat_count=template.count,
                metadata={
                    "burdenPct": round(template.count / bank.beats_seen * 100.0, 4),
                    "meanIntraCorrelation": round(compactness, 6),
                    "beatsSeen": bank.beats_seen,
                },
            )
        )
    return findings


def _metrics(
    totals: dict[str, float],
    report: QualityReport,
    reported: tuple[QualityWindow, ...],
    analyzed_beats: int,
    bank: TemplateBank,
) -> dict[str, float]:
    metrics = summary_from_totals(totals)
    metrics.update(
        {
            "analyzedBeats": float(analyzed_beats),
            "templates": float(len(bank.templates)),
            "beatsSeen": float(bank.beats_seen),
            "unmatchedBeats": float(bank.unmatched_beats),
            "firmwarePeaks": 1.0 if report.firmware_peaks_available else 0.0,
        }
    )
    bsqi = [window.bsqi for window in reported if window.bsqi is not None]
    if bsqi:
        metrics["medianBsqi"] = round(float(np.median(bsqi)), 6)
    return metrics
