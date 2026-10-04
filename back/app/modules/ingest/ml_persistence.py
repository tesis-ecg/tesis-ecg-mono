"""Persistencia de lo que produce el motor de detección.

Separado de `processing.py` porque tiene una responsabilidad propia: escribir
hallazgos, calidad y banco de plantillas sin duplicar nada ni inflar los conteos.

**Escribe por bloque, no por lote.** El motor corre sobre bloques de la corrida
detrás de un cursor (`processing.append_ml_analysis`), con un contexto izquierdo
que ya analizó el bloque anterior. Todo lo de acá corre dentro de la
transacción del lote que disparó el análisis (o de la finalización del
estudio): si algo falla, el rollback se lleva junto los eventos, la calidad,
las alertas, `study.ml_state` y el cursor, y el reintento vuelve a analizar el
mismo bloque desde el mismo estado. No hay un borrado previo por bloque.

Cuatro reglas:

1. `model_version IS NOT NULL` es el único predicado que dice "esto lo escribió el
   motor". Los hallazgos manuales de `simulate-anomaly`, los seeds legacy y la
   Capa A (`"source": "firmware_flags"`, que escribe `processing._persist_events`)
   lo tienen en NULL y **nunca se tocan**.
2. **Un episodio que cruza el borde de un bloque es un solo evento.** Un
   hallazgo de ritmo o un episodio de morfología puede empezar en el contexto,
   y una banda de ruido puede arrancar justo donde terminó la del bloque
   anterior. En vez de insertar una segunda fila se **empalma** con el evento
   que ya existe de la misma corrida (`_stitch`), con la misma regla con que el
   motor funde dentro de un bloque: los de ritmo, a menos de la ventana de
   refractariedad. Una taquicardia de diez minutos termina siendo una fila, no
   una por bloque, y un aviso, no dos.
3. `dedupe_key` hace idempotente la escritura de un mismo hallazgo: los episodios
   que no se empalman entran con `ON CONFLICT DO NOTHING` y los encabezados por
   morfología se upsertean, así que su conteo refleja el total del estudio. La
   calidad, con `(study_id, start_sample_index)`. Con el rollback de arriba es
   una red de seguridad, no el mecanismo del reintento.
4. El banco no vuelve a plegar el último bloque que plegó (`lastFoldKey`, la
   clave del bloque): la misma red de seguridad, del lado de los conteos de
   plantillas.

Aparte de lo clínico, cada bloque medido deja una fila de
`ecg_interval_measurement` (QT, QTc y amplitud R): dato de investigación que no
lee ninguna API (ver el modelo). Misma idempotencia que la calidad, por
`(study_id, start_sample_index)`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.s3 import get_object, put_object
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.ecg_interval_measurement import ECGIntervalMeasurement
from app.db.models.signal_quality import SignalQualityInterval
from app.db.models.study import Study
from app.ml import morphology
from app.ml.contracts import Finding
from app.ml.episodes import _merge_metadata
from app.ml.morphology import TemplateBank
from app.ml.pipeline import PIPELINE_VERSION, PipelineConfig, PipelineResult, empty_bank
from app.ml.totals import summary_from_totals
from app.modules.ingest.pushable import PUSH_RANK, Pushable, most_severe

#: Predicado de `uq_ecg_event_dedupe`. Postgres **no infiere un índice parcial**
#: solo por sus columnas: sin repetir el WHERE, el `ON CONFLICT` falla con "there
#: is no unique or exclusion constraint matching the ON CONFLICT specification".
#: Tiene que coincidir literalmente con el de la migración.
_DEDUPE_WHERE = ECGEvent.dedupe_key.is_not(None) & ECGEvent.deleted_at.is_(None)

_SEVERITY_RANK = {
    ECGEventSeverity.LOW: 0,
    ECGEventSeverity.MEDIUM: 1,
    ECGEventSeverity.HIGH: 2,
    ECGEventSeverity.CRITICAL: 3,
}


@dataclass(frozen=True, slots=True)
class BlockScope:
    """Dónde cae el bloque que se persiste y a qué lote se atribuye.

    `batch_id` es atribución y nunca NULL: el lote que disparó el análisis, o en
    la finalización el que cubre el final del bloque
    (`processing._attribution_batch`). `boot_id` es el de la corrida y no el del
    lote: el bloque que cierra una corrida lo puede disparar el primer lote de
    la siguiente, que ya viene de otro arranque del equipo.
    """

    batch_id: uuid.UUID
    boot_id: int | None
    #: Inicio de la corrida (`study_timeline_segment`). Los empalmes no la
    #: cruzan: dos corridas son contiguas en el buffer empaquetado pero entre
    #: ellas hay un hueco real de grabación.
    run_start: int
    #: Primera muestra del contexto, `block_start` si no hay.
    read_start: int
    block_start: int
    block_end: int
    #: Primera muestra cuyo hallazgo todavía puede mandarle un push al paciente
    #: (`processing._push_from_sample`, por `ml_push_max_age_minutes`). Un
    #: hallazgo que termina antes se escribe con su alerta igual: es para el
    #: médico, no para despertar al paciente por algo de hace horas.
    push_from: int = 0


def templates_key(study_id: uuid.UUID, version: int, *, merged: bool = False) -> str:
    """Clave **versionada por cursor**, mismo patrón que los segmentos.

    Sobrescribir una clave fija haría que una pasada que después falla y hace
    rollback deje en S3 un banco que ya no corresponde a lo que dice la base. Con
    la clave versionada, un rollback deja como mucho un objeto huérfano —barato—
    y nunca una lectura incoherente. La versión es el final del último bloque
    plegado; la fusión del cierre lleva su propio sufijo para no pisar la clave
    del bloque que la base todavía referencia si la fusión no llega a commitear.
    """
    suffix = ".merged" if merged else ""
    return f"studies/{study_id}/ml/templates.{version:012d}{suffix}.f32"


def load_bank(state: dict[str, Any], config: PipelineConfig) -> TemplateBank:
    """Reconstruye el banco del estudio. Vacío si no hay o si no es compatible.

    Hoy corre en línea, en el event loop y con la fila del estudio tomada: el
    GET de S3 bloquea mientras dura, igual que el resto de los `get_object` del
    procesamiento. Recibe el dict de `study.ml_state` y no el `Study` para poder
    sacarlo a un hilo sin cambiar la firma: tocar ahí un atributo de una entidad
    expirada dispararía un refresh lazy de SQLAlchemy fuera del greenlet.
    """
    key = state.get("templatesKey")
    blob = b""
    if isinstance(key, str) and key:
        try:
            blob = get_object(key)
        except Exception:  # noqa: BLE001 — el objeto se puede haber ido
            blob = b""
    bank = morphology.bank_from_state(state, blob, model_version=PIPELINE_VERSION)
    if bank.beat_length == 0:
        return empty_bank(config)
    return bank


def study_metrics(totals: Mapping[str, float], bank: TemplateBank) -> dict[str, float]:
    """`ml_state["metrics"]`: el resumen del estudio entero más el estado del banco.

    Sale de los totales acumulados y no del último bloque. Antes era el
    resumen del último lote analizado, que sobre un estudio de quince días
    describía quince segundos.
    """
    metrics = summary_from_totals(totals)
    metrics.update(
        {
            "templates": float(len(bank.templates)),
            "beatsSeen": float(bank.beats_seen),
            "unmatchedBeats": float(bank.unmatched_beats),
        }
    )
    return metrics


def store_bank(
    study: Study,
    bank: TemplateBank,
    version: int,
    totals: Mapping[str, float] | None = None,
    *,
    merged: bool = False,
) -> None:
    """Escribe los centroides en S3 y la metadata liviana en `study.ml_state`.

    `totals` son los del estudio ya combinados; sin ellos se conservan los que
    había, que es lo que necesita la fusión del cierre (no analiza señal nueva).
    `bank_to_state` arma el estado desde cero, así que cualquier clave que no
    sea del banco se tiene que volver a poner acá o se pierde.
    """
    previous: dict[str, Any] = study.ml_state or {}
    kept = dict(previous.get("totals") or {}) if totals is None else dict(totals)
    state, blob = morphology.bank_to_state(bank)
    if blob:
        key = templates_key(study.id, version, merged=merged)
        put_object(key, blob)
        state["templatesKey"] = key
    state["totals"] = kept
    state["metrics"] = study_metrics(kept, bank)
    # Reasignación y no mutación: SQLAlchemy no detecta cambios in-place sobre un
    # JSONB y el UPDATE no se emitiría.
    study.ml_state = state


# --------------------------------------------------------------------------- #
# Eventos
# --------------------------------------------------------------------------- #


async def count_anomalies(db: AsyncSession, study_id: uuid.UUID) -> int:
    """Anomalías por episodio ya escritas para el estudio (sin los encabezados)."""
    total = await db.scalar(
        select(func.count())
        .select_from(ECGEvent)
        .where(
            ECGEvent.study_id == study_id,
            ECGEvent.event_type == ECGEventType.ANOMALY,
            ECGEvent.deleted_at.is_(None),
            ECGEvent.model_version.is_not(None),
            ECGEvent.event_metadata["scope"].astext == "batch",
        )
    )
    return int(total or 0)


def _event_row(
    finding: Finding, study: Study, scope: BlockScope, sample_rate: int
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "kind": finding.kind,
        "studyId": str(study.id),
        "startSampleIndex": finding.start_sample,
        "sampleCount": finding.length_samples,
        "bootId": scope.boot_id,
        "source": "ml",
        "scope": finding.scope,
        **finding.metadata,
    }
    if finding.cluster_id is not None:
        metadata["clusterId"] = finding.cluster_id
    if finding.beat_count is not None:
        metadata["beatCount"] = finding.beat_count
    return {
        "id": uuid.uuid4(),
        "batch_id": scope.batch_id,
        "study_id": study.id,
        "event_type": finding.event_type,
        "severity": finding.severity,
        "timestamp_in_recording": finding.start_sample / sample_rate,
        "duration_seconds": finding.length_samples / sample_rate,
        "confidence_score": finding.score,
        "event_metadata": metadata,
        "model_version": PIPELINE_VERSION,
        "dedupe_key": finding.dedupe_key,
    }


#: Los que el motor funde con `episodes.apply_refractory` dentro de un bloque:
#: los de ritmo, los únicos que avisan al paciente. A través del borde se
#: empalman con la misma ventana. Los de calidad y los de morfología no pasan
#: por la refractariedad (`pipeline.analyze_batch` explica por qué) y se
#: empalman solo si se tocan o se solapan.
_REFRACTORY_KINDS = frozenset({"tachycardia", "bradycardia", "pause"})


def _stitchable(finding: Finding) -> bool:
    """Todo episodio de bloque: ritmo, calidad y morfología.

    Los de morfología también: el motor agrupa los latidos del contexto con los
    nuevos, así que un bigeminismo que cruza el borde llega como un episodio que
    arranca en el contexto y se solapa con el que escribió el bloque anterior.
    """
    return finding.scope == "batch"


def _stitch_reach(finding: Finding, sample_rate: int) -> int:
    """Cuánto puede separarse un evento del hallazgo y seguir siendo el mismo.

    Para los de ritmo, la ventana de refractariedad. La fusión de
    `apply_refractory` dentro del bloque depende de que el primer episodio se
    vuelva a ver en el contexto, y no se ve si lo que entra del contexto no
    llega al mínimo de `ml_rhythm_min_seconds`: una taquicardia que terminó 25 s
    antes del borde y otra que empieza 6 s después eran, de una sola vez, una;
    por bloques, dos eventos y dos avisos.
    """
    if finding.kind in _REFRACTORY_KINDS:
        return int(settings.ml_episode_refractory_seconds * sample_rate)
    return 0


def _span(event: ECGEvent) -> tuple[int, int]:
    metadata = event.event_metadata or {}
    start = int(metadata.get("startSampleIndex", 0))
    return start, start + int(metadata.get("sampleCount", 0))


async def _stitch_candidates(
    db: AsyncSession,
    study: Study,
    scope: BlockScope,
    kinds: set[str],
    sample_rate: int,
) -> list[ECGEvent]:
    """Eventos del motor de la misma corrida que todavía llegan al contexto.

    El filtro grueso va por `timestamp_in_recording`, que tiene índice; el
    exacto, en muestras enteras, lo hace `_stitch`. El margen de un segundo
    absorbe el redondeo de pasar muestras a segundos en float, y la ventana de
    refractariedad, a los de ritmo que terminaron poco antes del contexto.
    """
    reach = settings.ml_episode_refractory_seconds
    rows = await db.scalars(
        select(ECGEvent)
        .where(
            ECGEvent.study_id == study.id,
            ECGEvent.deleted_at.is_(None),
            ECGEvent.model_version.is_not(None),
            ECGEvent.event_metadata["scope"].astext == "batch",
            ECGEvent.event_metadata["kind"].astext.in_(sorted(kinds)),
            ECGEvent.timestamp_in_recording >= scope.run_start / sample_rate - 1.0,
            ECGEvent.timestamp_in_recording + func.coalesce(ECGEvent.duration_seconds, 0.0)
            >= scope.read_start / sample_rate - 1.0 - reach,
        )
        .order_by(ECGEvent.timestamp_in_recording)
    )
    return [event for event in rows.all() if _span(event)[0] >= scope.run_start]


def _covered(start: int, end: int, spans: Sequence[tuple[int, int]]) -> int:
    """Muestras de `[start, end)` que ya cubre alguno de `spans`."""
    clipped = sorted((max(a, start), min(b, end)) for a, b in spans if b > start and a < end)
    covered, cursor = 0, start
    for a, b in clipped:
        a = max(a, cursor)
        if b > a:
            covered += b - a
            cursor = b
    return covered


def _inside(sample: int, spans: Sequence[tuple[int, int]]) -> bool:
    """Si algún tramo cubre la muestra, **con** su borde final.

    El final de un hallazgo de ritmo es su último R, que el hallazgo cuenta
    (`arrhythmia.detect_rhythm`): un tramo semiabierto lo dejaría afuera y el
    empalme lo volvería a sumar.
    """
    return any(low <= sample <= high for low, high in spans)


def _stitch(
    candidates: list[ECGEvent], finding: Finding, sample_rate: int
) -> tuple[ECGEvent, list[ECGEvent]] | None:
    """Extiende el evento que el hallazgo toca o solapa. `None` si no toca ninguno.

    "Toca" incluye la adyacencia exacta (`fin == inicio`): una banda de ruido del
    bloque nuevo que arranca en la muestra donde terminó la del anterior es la
    misma banda. Los de ritmo se empalman además a través de un hueco de hasta
    la ventana de refractariedad (`_stitch_reach`), y los de morfología solo con
    un evento del mismo foco (`clusterId`). El sobreviviente es el más viejo y
    conserva su `id`, su `dedupe_key` y su lote; si el hallazgo une dos eventos
    que antes estaban separados, el otro se da de baja (lo devuelve en la lista
    de absorbidos para que sus alertas pasen al sobreviviente).

    `beatCount` no se puede sumar: el hallazgo nuevo recuenta latidos que el
    evento ya tenía. Se le suman los R del hallazgo (`Finding.beat_samples`)
    que caen afuera de lo que los eventos ya cubrían: los latidos del evento
    empalmado son la unión de los de cada bloque que lo informó, que es lo que
    cuenta el análisis de una sola vez. Antes se estimaba por la fracción del
    largo que caía afuera, con el ritmo del hallazgo, y erraba justo donde los
    latidos no son parejos: un foco ectópico que se acelera sobre el borde daba
    9 latidos donde había 11. La estimación queda solo para un hallazgo sin
    posiciones.
    """
    start = finding.start_sample
    end = start + finding.length_samples
    reach = _stitch_reach(finding, sample_rate)
    matches = [
        event
        for event in candidates
        if (event.event_metadata or {}).get("kind") == finding.kind
        and (event.event_metadata or {}).get("clusterId") == finding.cluster_id
        and _span(event)[0] <= end + reach
        and _span(event)[1] >= start - reach
    ]
    if not matches:
        return None
    matches.sort(key=lambda event: _span(event)[0])
    survivor, absorbed = matches[0], matches[1:]
    spans = [_span(event) for event in matches]
    union_start = min(start, *(a for a, _ in spans))
    union_end = max(end, *(b for _, b in spans))
    length = union_end - union_start

    previous: dict[str, Any] = dict(survivor.event_metadata or {})
    for other in absorbed:
        previous = _merge_metadata(previous, dict(other.event_metadata or {}), length, sample_rate)
    metadata: dict[str, Any] = _merge_metadata(previous, finding.metadata, length, sample_rate)
    if not absorbed and spans[0] == (union_start, union_end) and "medianBpm" in previous:
        # Un hallazgo que no lo agranda —el mismo episodio vuelto a informar
        # desde el contexto— no lo vuelve un episodio fundido: su mediana sigue
        # siendo la suya.
        metadata["medianBpm"] = previous["medianBpm"]
    metadata["startSampleIndex"] = union_start
    metadata["sampleCount"] = length
    beats = [
        int(value)
        for value in ((event.event_metadata or {}).get("beatCount") for event in matches)
        if isinstance(value, int)
    ]
    if finding.beat_samples:
        beats.append(sum(1 for sample in finding.beat_samples if not _inside(sample, spans)))
    elif finding.beat_count is not None and finding.length_samples > 0:
        outside = finding.length_samples - _covered(start, end, spans)
        beats.append(round(finding.beat_count * outside / finding.length_samples))
    if beats:
        metadata["beatCount"] = sum(beats)

    survivor.event_metadata = metadata
    survivor.timestamp_in_recording = union_start / sample_rate
    survivor.duration_seconds = length / sample_rate
    scores = [
        value
        for value in (finding.score, *(event.confidence_score for event in matches))
        if value is not None
    ]
    survivor.confidence_score = max(scores) if scores else None
    survivor.severity = max(
        (finding.severity, *(event.severity for event in matches)),
        key=lambda value: _SEVERITY_RANK[value],
    )
    now = datetime.now(UTC)
    for other in absorbed:
        other.deleted_at = now
        candidates.remove(other)
    return survivor, absorbed


async def persist_analysis(
    db: AsyncSession,
    study: Study,
    result: PipelineResult,
    sample_rate: int,
    scope: BlockScope,
) -> tuple[int, Pushable | None]:
    """Escribe hallazgos y calidad de un bloque. Devuelve `(cuántos, alerta a notificar)`.

    El banco no: lo guarda `processing.append_ml_analysis` una vez por pasada,
    después del último bloque (`store_bank`). La medición de intervalos sí, si
    el bloque tiene (`_persist_interval_measurement`).

    `cuántos` son los eventos escritos, actualizados o empalmados, para el log:
    el `events_count` del estudio sale de `recount_events`, no de acá.

    La alerta viaja hacia arriba en vez de notificarse acá: la transacción
    todavía no cerró, y mandar un push con un `alertId` que después se descarta
    dejaría al paciente tocando una notificación rota. Es el mismo `Pushable` de
    la Capa A, con su `kind`, así que el aviso de una pausa del motor se titula
    igual que cualquier otro (`anomaly_title`).
    """
    findings = list(result.findings)
    # Los encabezados por morfología se **upsertean**: su conteo de
    # ocurrencias crece bloque a bloque y la fila tiene que reflejar el total
    # del estudio, no el del último bloque.
    study_rows = [
        _event_row(finding, study, scope, sample_rate)
        for finding in findings
        if finding.scope == "study"
    ]
    if study_rows:
        statement = pg_insert(ECGEvent).values(study_rows)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[ECGEvent.study_id, ECGEvent.dedupe_key],
                index_where=_DEDUPE_WHERE,
                set_={
                    "duration_seconds": statement.excluded.duration_seconds,
                    "confidence_score": statement.excluded.confidence_score,
                    # Por NOMBRE DE COLUMNA y no por atributo del modelo: la
                    # columna está mapeada como `metadata`, y `excluded` se
                    # indexa por columna. Como atributo tampoco se puede —
                    # `metadata` colisiona con `Table.metadata`.
                    "metadata": statement.excluded["metadata"],
                    "severity": statement.excluded.severity,
                    "timestamp_in_recording": statement.excluded.timestamp_in_recording,
                    "batch_id": statement.excluded.batch_id,
                    "model_version": statement.excluded.model_version,
                },
            )
        )

    stitchable = sorted(
        (finding for finding in findings if _stitchable(finding)),
        key=lambda finding: finding.start_sample,
    )
    to_insert: list[Finding] = []
    extended: list[tuple[Finding, ECGEvent]] = []
    if stitchable:
        candidates = await _stitch_candidates(
            db, study, scope, {finding.kind for finding in stitchable}, sample_rate
        )
        for finding in stitchable:
            stitched = _stitch(candidates, finding, sample_rate)
            if stitched is None:
                to_insert.append(finding)
                continue
            survivor, absorbed = stitched
            extended.append((finding, survivor))
            if absorbed:
                await db.execute(
                    update(Alert)
                    .where(Alert.event_id.in_([event.id for event in absorbed]))
                    .values(event_id=survivor.id)
                )
        await db.flush()

    inserted: dict[str, uuid.UUID] = {}
    if to_insert:
        # Un bulk insert y no un `db.add()` + `flush()` por evento: con
        # cientos de hallazgos serían cientos de round-trips a Postgres.
        statement = pg_insert(ECGEvent).values(
            [_event_row(finding, study, scope, sample_rate) for finding in to_insert]
        )
        written = await db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[ECGEvent.study_id, ECGEvent.dedupe_key],
                index_where=_DEDUPE_WHERE,
            ).returning(ECGEvent.id, ECGEvent.dedupe_key)
        )
        inserted = {key: event_id for event_id, key in written.all()}

    pushable = await _create_alerts(
        db,
        study,
        [(finding, inserted.get(finding.dedupe_key)) for finding in to_insert],
        push_from=scope.push_from,
    )
    pushable = most_severe(
        pushable, await _escalation_alerts(db, study, extended, push_from=scope.push_from)
    )

    if result.quality_intervals:
        await db.execute(
            pg_insert(SignalQualityInterval)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "study_id": study.id,
                        "batch_id": scope.batch_id,
                        "start_sample_index": interval.start_sample,
                        "sample_count": interval.length_samples,
                        "level": interval.level,
                        "reason": interval.reason,
                        "window_count": count,
                        "metrics": {
                            "psqi": interval.psqi,
                            "ksqi": interval.ksqi,
                            "bassqi": interval.bassqi,
                            "bsqi": interval.bsqi,
                        },
                        "model_version": result.model_version,
                    }
                    for interval, count in result.quality_intervals
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    SignalQualityInterval.study_id,
                    SignalQualityInterval.start_sample_index,
                ],
                index_where=SignalQualityInterval.deleted_at.is_(None),
            )
        )

    await _persist_interval_measurement(db, study, result, scope)

    return len(study_rows) + len(inserted) + len(extended), pushable


async def _persist_interval_measurement(
    db: AsyncSession, study: Study, result: PipelineResult, scope: BlockScope
) -> None:
    """Una fila de `ecg_interval_measurement` por bloque medido; ninguna si no se midió.

    El bloque es la parte nueva (`block_start`, `block_end`): las medianas son
    de los latidos con el R ahí (`pipeline._measure_intervals`). No entra en
    el conteo que devuelve `persist_analysis`, que es de eventos.
    """
    measurement = result.intervals
    if measurement is None:
        return
    await db.execute(
        pg_insert(ECGIntervalMeasurement)
        .values(
            id=uuid.uuid4(),
            study_id=study.id,
            batch_id=scope.batch_id,
            start_sample_index=scope.block_start,
            sample_count=scope.block_end - scope.block_start,
            beats=measurement.beats,
            candidate_beats=measurement.candidate_beats,
            coverage_ratio=measurement.coverage_ratio,
            qt_ms=measurement.qt_ms,
            qtc_ms=measurement.qtc_ms,
            r_amplitude_mv=measurement.r_amplitude_mv,
            heart_rate_bpm=measurement.heart_rate_bpm,
            candidate_heart_rate_bpm=measurement.candidate_heart_rate_bpm,
            qrs_ms=measurement.qrs_ms,
            method=measurement.method,
            experimental=measurement.experimental,
            model_version=result.model_version,
        )
        .on_conflict_do_nothing(
            index_elements=[
                ECGIntervalMeasurement.study_id,
                ECGIntervalMeasurement.start_sample_index,
            ]
        )
    )


async def _create_alerts(
    db: AsyncSession,
    study: Study,
    findings: Sequence[tuple[Finding, uuid.UUID | None]],
    *,
    push_from: int = 0,
) -> Pushable | None:
    """Una alerta por hallazgo realmente nuevo que la pida.

    Solo por los que el `INSERT` escribió (id no nulo): un hallazgo cuyo
    `dedupe_key` ya existía no puede generar un segundo push del mismo evento.
    El push, además, solo si el hallazgo termina en `push_from` o después
    (`BlockScope.push_from`): la alerta de uno viejo queda para el médico.
    """
    pushable: Pushable | None = None
    for finding, event_id in findings:
        if finding.alert_message is None or event_id is None:
            continue
        alert = Alert(
            patient_id=study.patient_id,
            event_id=event_id,
            kind=finding.kind,
            severity=AlertSeverity[finding.severity.name],
            message=finding.alert_message,
        )
        db.add(alert)
        await db.flush()
        rank = PUSH_RANK.get(finding.severity)
        if rank is not None and finding.start_sample + finding.length_samples >= push_from:
            pushable = most_severe(
                pushable, Pushable(rank=rank, alert_id=alert.id, kind=finding.kind)
            )
    return pushable


async def _escalation_alerts(
    db: AsyncSession,
    study: Study,
    extended: Sequence[tuple[Finding, ECGEvent]],
    *,
    push_from: int = 0,
) -> Pushable | None:
    """Como mucho **una** alerta por evento, también cuando se empalma, y con su severidad.

    Una extensión no vuelve a avisar: el análisis del registro de una sola vez
    habría producido un solo evento y un solo aviso. Pero tampoco puede callar
    lo que ese análisis sí habría dicho:

    - una taquicardia que empieza moderada (sin aviso) y en el bloque siguiente
      pasa el umbral severo es, de una sola vez, un evento severo con su aviso:
      avisa el empalme que pide alerta sobre un evento que todavía no tiene;
    - una pausa de 2,7 s (alta, avisada) seguida a 5 s de otra de 3,5 s en el
      bloque siguiente es, de una sola vez, **una** pausa crítica con un aviso
      crítico. El evento empalmado ya sube a crítico (`_stitch`); su alerta
      también tiene que subir, o se ordena y se filtra en la bandeja del médico
      como alta. Se escala esa misma alerta —vuelve a no vista y sin reconocer,
      porque es información nueva— y se vuelve a notificar: el paciente había
      recibido un aviso de menor severidad.

    El push, con la misma regla de antigüedad que `_create_alerts`, sobre el
    final del evento ya empalmado.
    """
    asking = [(finding, event) for finding, event in extended if finding.alert_message]
    if not asking:
        return None
    existing: dict[uuid.UUID, Alert] = {}
    for alert in (
        await db.scalars(select(Alert).where(Alert.event_id.in_([event.id for _, event in asking])))
    ).all():
        if alert.event_id is None:
            continue
        current = existing.get(alert.event_id)
        if current is None or _alert_rank(alert) > _alert_rank(current):
            existing[alert.event_id] = alert

    pushable: Pushable | None = None
    for finding, event in asking:
        severity = event.severity
        current = existing.get(event.id)
        if current is None:
            current = Alert(
                patient_id=study.patient_id,
                event_id=event.id,
                kind=finding.kind,
                severity=AlertSeverity[severity.name],
                message=finding.alert_message,
            )
            db.add(current)
            await db.flush()
            existing[event.id] = current
        elif _alert_rank(current) < _SEVERITY_RANK[severity]:
            current.severity = AlertSeverity[severity.name]
            current.message = finding.alert_message or current.message
            current.seen_at = None
            current.acknowledged_at = None
            current.acknowledged_by = None
        else:
            continue
        rank = PUSH_RANK.get(severity)
        if rank is not None and _span(event)[1] >= push_from:
            pushable = most_severe(
                pushable, Pushable(rank=rank, alert_id=current.id, kind=finding.kind)
            )
    return pushable


def _alert_rank(alert: Alert) -> int:
    return _SEVERITY_RANK[ECGEventSeverity[alert.severity.name]]


async def recount_events(db: AsyncSession, study: Study) -> None:
    """Recalcula `study.events_count` en vez de incrementarlo.

    Hay dos escritores (la Capa A en `processing._persist_events` y el motor
    acá), los encabezados por morfología se upsertean, los episodios que cruzan
    un borde de bloque se empalman y `consolidate_morphologies` y los empalmes
    dan de baja los absorbidos: un `+= creados` tendría que saber distinguir
    altas de actualizaciones y de bajas en todos. El `COUNT(*)` sobre
    `ix_ecg_event_study_ts` con unos cientos de filas es gratis y elimina la
    clase entera de bugs.
    """
    total = await db.scalar(
        select(func.count())
        .select_from(ECGEvent)
        .where(ECGEvent.study_id == study.id, ECGEvent.deleted_at.is_(None))
    )
    study.events_count = int(total or 0)


# --------------------------------------------------------------------------- #
# Cierre del estudio
# --------------------------------------------------------------------------- #


async def consolidate_morphologies(db: AsyncSession, study: Study) -> dict[int, int]:
    """Funde morfologías que derivaron hacia la misma forma. Devuelve `viejo → nuevo`.

    Corre en la finalización de un estudio completado
    (`processing.process_study_task`, después de drenar los lotes en cola y de
    analizar la cola de la última corrida) y
    sobre ≤ 40 centroides — nunca sobre los 100.000 latidos. Existe porque el
    banco es *greedy*: si en la hora 2 aparece una forma intermedia entre dos
    plantillas, puede haber abierto dos donde había una sola morfología. Al
    final se ve el estudio completo y se corrige. La finalización puede volver
    a correr (un lote que llegó tarde, la recuperación desde el manifest): sin
    nada que fundir no escribe nada.

    Los `ecg_event` de las plantillas absorbidas se reescriben al id
    sobreviviente —el más viejo—, así que las filas escritas con ese id siguen
    siendo válidas y solo cambia lo mínimo.
    """
    state: dict[str, Any] = study.ml_state or {}
    key = state.get("templatesKey")
    if not isinstance(key, str) or not key:
        return {}
    try:
        blob = get_object(key)
    except Exception:  # noqa: BLE001 — un estudio sin banco no tiene nada que fundir
        return {}

    bank = morphology.bank_from_state(state, blob, model_version=PIPELINE_VERSION)
    if len(bank.templates) < 2:
        return {}
    merged, mapping = morphology.consolidate(
        bank, merge_threshold=settings.ml_template_merge_threshold
    )
    if not mapping:
        return {}

    events = list(
        (
            await db.scalars(
                select(ECGEvent).where(
                    ECGEvent.study_id == study.id,
                    ECGEvent.deleted_at.is_(None),
                    ECGEvent.model_version.is_not(None),
                )
            )
        ).all()
    )
    absorbed_keys = {f"cluster:{old}" for old in mapping}
    for event in events:
        metadata = dict(event.event_metadata or {})
        cluster_id = metadata.get("clusterId")
        if not isinstance(cluster_id, int) or cluster_id not in mapping:
            continue
        if event.dedupe_key in absorbed_keys:
            # El encabezado de una plantilla absorbida deja de existir: su
            # conteo ya se sumó al del sobreviviente en `consolidate`.
            event.deleted_at = datetime.now(UTC)
            continue
        metadata["clusterId"] = mapping[cluster_id]
        event.event_metadata = metadata

    # El encabezado sobreviviente tiene que reflejar el conteo fusionado.
    by_key = {event.dedupe_key: event for event in events}
    for template in merged.templates:
        header = by_key.get(f"cluster:{template.cluster_id}")
        if header is None or header.deleted_at is not None:
            continue
        metadata = dict(header.event_metadata or {})
        metadata["beatCount"] = template.count
        metadata["burdenPct"] = round(template.count / max(merged.beats_seen, 1) * 100.0, 4)
        header.event_metadata = metadata
        header.duration_seconds = max(template.last_sample - template.first_sample, 1) / (
            study.sample_rate or 500
        )

    # Versión del cursor con sufijo propio: sin señal nueva, la clave del último
    # bloque es la que la base sigue referenciando hasta que esto commitee. Sin
    # `totals`, `store_bank` conserva los del estudio.
    store_bank(study, merged, study.ml_analyzed_samples, merged=True)
    # Los encabezados absorbidos se dieron de baja: el conteo del estudio no
    # puede seguir contándolos.
    await recount_events(db, study)
    return mapping
