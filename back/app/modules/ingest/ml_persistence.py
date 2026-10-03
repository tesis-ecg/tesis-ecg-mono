"""Persistencia de lo que produce el motor de detección.

Separado de `processing.py` porque tiene una responsabilidad propia: escribir
hallazgos, calidad y banco de plantillas sin duplicar nada ni inflar los conteos.

**Un lote que falla no deja nada a medias.** Todo lo de acá corre dentro de la
transacción del lote: si algo falla, `processing._mark_failed` hace rollback de
los eventos, la calidad, las alertas y `study.ml_state` juntos, y el reintento
arranca desde el mismo estado. Por eso no hay un borrado previo por lote: un
lote `DONE` no se vuelve a procesar y uno `FAILED` no dejó nada escrito.

Tres reglas:

1. `model_version IS NOT NULL` es el único predicado que dice "esto lo escribió el
   motor". Los hallazgos manuales de `simulate-anomaly`, los seeds legacy y la
   Capa A (`"source": "firmware_flags"`, que escribe `processing._persist_events`)
   lo tienen en NULL y **nunca se tocan**.
2. `dedupe_key` hace idempotente la escritura de un mismo hallazgo: los episodios
   entran con `ON CONFLICT DO NOTHING` y los encabezados por morfología se
   upsertean, así que su conteo refleja el total del estudio. Con la regla de
   arriba es una red de seguridad, no el mecanismo del reintento.
3. El banco no vuelve a plegar el último lote que plegó (`lastFoldedBatchId`):
   la misma red de seguridad, del lado de los conteos de plantillas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.s3 import get_object, put_object
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent, ECGEventType
from app.db.models.signal_quality import SignalQualityInterval
from app.db.models.study import Study
from app.ml import morphology
from app.ml.contracts import Finding
from app.ml.morphology import TemplateBank
from app.ml.pipeline import PIPELINE_VERSION, PipelineConfig, PipelineResult, empty_bank
from app.modules.ingest.pushable import PUSH_RANK, Pushable, most_severe

#: Predicado de `uq_ecg_event_dedupe`. Postgres **no infiere un índice parcial**
#: solo por sus columnas: sin repetir el WHERE, el `ON CONFLICT` falla con "there
#: is no unique or exclusion constraint matching the ON CONFLICT specification".
#: Tiene que coincidir literalmente con el de la migración.
_DEDUPE_WHERE = ECGEvent.dedupe_key.is_not(None) & ECGEvent.deleted_at.is_(None)


def templates_key(study_id: uuid.UUID, first_seq: int) -> str:
    """Clave **versionada por lote**, mismo patrón que los segmentos.

    Sobrescribir una clave fija haría que un lote que después falla y hace
    rollback deje en S3 un banco que ya no corresponde a lo que dice la base. Con
    la clave versionada, un rollback deja como mucho un objeto huérfano —barato—
    y nunca una lectura incoherente.
    """
    return f"studies/{study_id}/ml/templates.{first_seq:012d}.f32"


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


def store_bank(study: Study, bank: TemplateBank, first_seq: int, metrics: dict[str, float]) -> None:
    """Escribe los centroides en S3 y la metadata liviana en `study.ml_state`."""
    state, blob = morphology.bank_to_state(bank)
    if blob:
        key = templates_key(study.id, first_seq)
        put_object(key, blob)
        state["templatesKey"] = key
    state["metrics"] = metrics
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


def _event_row(finding: Finding, batch: ECGBatch, study: Study, sample_rate: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "kind": finding.kind,
        "studyId": str(study.id),
        "startSampleIndex": finding.start_sample,
        "sampleCount": finding.length_samples,
        "bootId": batch.boot_id,
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
        "batch_id": batch.id,
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


async def persist_analysis(
    db: AsyncSession,
    study: Study,
    batch: ECGBatch,
    result: PipelineResult,
    sample_rate: int,
) -> tuple[int, Pushable | None]:
    """Escribe hallazgos, calidad y banco. Devuelve `(cuántos, alerta a notificar)`.

    `cuántos` son las filas escritas o actualizadas, para el log: el
    `events_count` del estudio sale de `recount_events`, no de acá.

    La alerta viaja hacia arriba en vez de notificarse acá: la transacción
    todavía no cerró, y mandar un push con un `alertId` que después se descarta
    dejaría al paciente tocando una notificación rota. Es el mismo `Pushable` de
    la Capa A, con su `kind`, así que el aviso de una pausa del motor se titula
    igual que cualquier otro (`anomaly_title`).
    """
    rows = [_event_row(finding, batch, study, sample_rate) for finding in result.findings]
    inserted: dict[str, uuid.UUID] = {}
    if rows:
        # Los encabezados por morfología se **upsertean**: su conteo de
        # ocurrencias crece lote a lote y la fila tiene que reflejar el total del
        # estudio, no el del último lote.
        study_rows = [row for row in rows if row["event_metadata"]["scope"] == "study"]
        batch_rows = [row for row in rows if row["event_metadata"]["scope"] == "batch"]
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
        if batch_rows:
            # Un bulk insert y no un `db.add()` + `flush()` por evento: con
            # cientos de hallazgos serían cientos de round-trips a Postgres.
            statement = pg_insert(ECGEvent).values(batch_rows)
            written = await db.execute(
                statement.on_conflict_do_nothing(
                    index_elements=[ECGEvent.study_id, ECGEvent.dedupe_key],
                    index_where=_DEDUPE_WHERE,
                ).returning(ECGEvent.id, ECGEvent.dedupe_key)
            )
            inserted = {key: event_id for event_id, key in written.all()}

    pushable = await _create_alerts(db, study, result.findings, inserted)

    if result.quality_intervals:
        await db.execute(
            pg_insert(SignalQualityInterval)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "study_id": study.id,
                        "batch_id": batch.id,
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
                    SignalQualityInterval.batch_id,
                    SignalQualityInterval.start_sample_index,
                ],
                index_where=SignalQualityInterval.deleted_at.is_(None),
            )
        )

    store_bank(study, result.bank, batch.first_seq or 0, result.metrics)
    return len(rows), pushable


async def _create_alerts(
    db: AsyncSession,
    study: Study,
    findings: tuple[Finding, ...],
    inserted: dict[str, uuid.UUID],
) -> Pushable | None:
    """Una alerta por hallazgo realmente nuevo que la pida.

    Solo por los que el `INSERT` escribió: un hallazgo cuyo `dedupe_key` ya
    existía no puede generar un segundo push del mismo evento.
    """
    pushable: Pushable | None = None
    for finding in findings:
        if finding.alert_message is None:
            continue
        event_id = inserted.get(finding.dedupe_key)
        if event_id is None:
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
        if rank is not None:
            pushable = most_severe(
                pushable, Pushable(rank=rank, alert_id=alert.id, kind=finding.kind)
            )
    return pushable


async def recount_events(db: AsyncSession, study: Study) -> None:
    """Recalcula `study.events_count` en vez de incrementarlo.

    Hay dos escritores (la Capa A en `processing._persist_events` y el motor
    acá), los encabezados por morfología se upsertean y `consolidate_morphologies`
    da de baja los absorbidos: un `+= creados` tendría que saber distinguir
    altas de actualizaciones y de bajas en los tres. El `COUNT(*)` sobre
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
    (`processing.process_study_task`, después de drenar los lotes en cola) y
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

    store_bank(study, merged, 0, dict(state.get("metrics") or {}))
    # Los encabezados absorbidos se dieron de baja: el conteo del estudio no
    # puede seguir contándolos.
    await recount_events(db, study)
    return mapping
