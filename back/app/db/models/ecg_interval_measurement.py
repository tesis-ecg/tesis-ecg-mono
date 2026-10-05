"""Mediciones de intervalos por bloque de análisis. **Dato de investigación.**

Una fila por bloque del motor (`processing.append_ml_analysis`) con las medianas
de QT, QTc de Fridericia, amplitud R y frecuencia de los latidos de su parte
nueva (`app/ml/intervals.py`). Existe para juntar datos del chaleco real para la
tesis, no para el producto: **ninguna API, ni el informe, ni el visor, ni un
hallazgo la leen**. La validación contra la QT Database dejó un QTc que casi no
sigue al del cardiólogo entre registros y que no puede ver un QT largo
(`tools/physionet/README.md`): mostrarlo como número del paciente lo haría
parecer normal siempre. Se lee con `app.scripts.export_interval_measurements`.

Por qué una tabla propia y no `ecg_event` ni `signal_quality_interval`: no es un
hallazgo (no tiene que aparecer en la bandeja ni en el visor) ni una propiedad
de cada instante del registro, sino un resumen por bloque.

**Solo bloques medidos.** Un bloque sin medición —medición apagada, menos de
`IntervalThresholds.min_beats` latidos válidos, más de la mitad de los
candidatos descartados por las guardas— no escribe fila. Una fila con todo en
NULL no diría nada que la falta de fila no diga, y el exportador tendría que
filtrarla; el denominador ("cuántos bloques se analizaron") ya está en
`signal_quality_interval`. Por eso todas las medidas son NOT NULL salvo
`qrs_ms`, que hoy es siempre NULL: el delineador lo topea en 100 ms por
construcción y no se reporta (ver `intervals.py`).

La clave natural es `(study_id, start_sample_index)`, el inicio de la parte
nueva del bloque, igual que la calidad: el cursor garantiza que cada muestra cae
en un solo bloque, y el índice único hace que reescribir el mismo bloque no
duplique nada (`ON CONFLICT DO NOTHING`). `batch_id` es atribución, con la misma
regla que el resto de las filas del motor (`ml_persistence.BlockScope`).

Las dos FK son `ON DELETE CASCADE`: la fila es derivada de la señal, y borrar un
estudio o sus lotes (los seeds lo hacen) no puede trabarse por un dato de
investigación que no le importa a nadie más.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ECGIntervalMeasurement(Base):
    __tablename__ = "ecg_interval_measurement"
    __table_args__ = (
        CheckConstraint("start_sample_index >= 0", name="ck_eim_start"),
        CheckConstraint("sample_count > 0", name="ck_eim_count"),
        CheckConstraint("beats > 0 AND beats <= candidate_beats", name="ck_eim_beats"),
        CheckConstraint("coverage_ratio > 0 AND coverage_ratio <= 1", name="ck_eim_coverage"),
        Index("uq_eim_study_start", "study_id", "start_sample_index", unique=True),
        Index("ix_eim_batch", "batch_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ecg_batch.id", ondelete="CASCADE"), nullable=False
    )
    #: Inicio de la parte nueva del bloque, absoluto al estudio. Sin el
    #: contexto: los latidos de ahí los midió el bloque anterior.
    start_sample_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Largo de la parte nueva.
    sample_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Latidos válidos, los que entran en las medianas.
    beats: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Latidos medibles en principio (`IntervalMeasurement.candidate_beats`).
    candidate_beats: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    qt_ms: Mapped[float] = mapped_column(Float, nullable=False)
    qtc_ms: Mapped[float] = mapped_column(Float, nullable=False)
    r_amplitude_mv: Mapped[float] = mapped_column(Float, nullable=False)
    #: FC de los latidos medidos: la del RR que entra al QTc.
    heart_rate_bpm: Mapped[float] = mapped_column(Float, nullable=False)
    #: FC de todos los candidatos. Si se aparta mucho de `heart_rate_bpm`, los
    #: latidos medidos no representan el ritmo del bloque.
    candidate_heart_rate_bpm: Mapped[float] = mapped_column(Float, nullable=False)
    #: Siempre NULL por ahora (ver el docstring del módulo).
    qrs_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Delineador (`intervals.METHOD`).
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    experimental: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: `pipeline.PIPELINE_VERSION`, como el resto de las filas del motor.
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
