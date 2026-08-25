"""Correlación alerta → estudio.

La alerta no guarda un FK al estudio: cuelga de un evento de ECG, y el estudio
recién aparece en el batch que trajo ese evento. Vive en un módulo compartido
porque la usan el listado de alertas y el widget del dashboard, y las dos
vistas tienen que linkear al mismo estudio.
"""

import uuid

from sqlalchemy import ColumnElement, func, or_, select

from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent
from app.db.models.study import Study


def alert_study_id() -> ColumnElement[uuid.UUID | None]:
    """El estudio al que lleva una alerta, con tres intentos en cascada.

    Requiere que la consulta que la usa ya tenga `Alert` y `ECGEvent` en el
    FROM: las tres ramas se correlacionan contra esas dos tablas.

    1. El batch del evento que disparó la alerta. Es el vínculo exacto, pero
       `ecg_batch.study_id` es nullable: los batches que llegan sin estudio
       abierto no lo tienen.
    2. El estudio del paciente que estaba corriendo cuando se detectó.
    3. El último estudio del paciente. Es aproximado, pero una alerta sin
       ningún estudio al que ir es una fila muerta en la tabla, y las alertas
       viejas (o las sembradas fuera de la ventana de su estudio) caían siempre
       en ese caso.
    """
    from_batch = (
        select(Study.id)
        .join(ECGBatch, ECGBatch.study_id == Study.id)
        .where(
            ECGBatch.id == ECGEvent.batch_id,
            ECGBatch.deleted_at.is_(None),
            Study.deleted_at.is_(None),
        )
        .limit(1)
        .scalar_subquery()
    )
    during_alert = (
        select(Study.id)
        .where(
            Study.patient_id == Alert.patient_id,
            Study.deleted_at.is_(None),
            Study.started_at <= Alert.created_at,
            or_(Study.ended_at.is_(None), Study.ended_at >= Alert.created_at),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    latest_of_patient = (
        select(Study.id)
        .where(Study.patient_id == Alert.patient_id, Study.deleted_at.is_(None))
        .order_by(Study.started_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    return func.coalesce(from_batch, during_alert, latest_of_patient)
