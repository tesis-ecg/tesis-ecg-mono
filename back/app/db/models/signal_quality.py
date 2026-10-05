"""Calidad de señal como propiedad continua del registro.

Un `ecg_event` es un hallazgo puntual: "acá pasó algo". La calidad no es eso —
**todo** instante del registro tiene un nivel de calidad, incluido el bueno. Un
Holter de 24 h son 8.640 ventanas de 10 s y escribirlas como eventos llenaría la
bandeja del médico de filas que no son hallazgos.

Por eso viven en su propia tabla y en forma de *run-length*: ventanas contiguas
del mismo nivel colapsan en un intervalo. Un bloque limpio de cinco minutos es
UNA fila, no 30.

**Un intervalo nunca cruza el borde de un bloque de análisis.** El motor no
corre por lote (~15 s) sino por bloques de la corrida detrás de un cursor
(`processing.append_ml_analysis`), y cada bloque escribe solo las ventanas de su
parte nueva. La clave natural de una fila es `(study_id, start_sample_index)`:
el cursor garantiza que cada muestra cae en un solo bloque, y el índice único
parcial hace que escribir dos veces el mismo bloque no duplique nada. `batch_id`
es atribución —el lote que disparó el análisis, o el que cubre el final del
bloque en el cierre—, no la clave. Fundir el run con la cola del bloque
anterior exigiría un UPDATE sobre una fila ya escrita; la fusión entre bloques
contiguos de la misma corrida se hace **al leer**, que es una pasada lineal
sobre unos cientos de filas.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.ecg_batch import ECGBatch
    from app.db.models.study import Study


class SignalQualityLevel(enum.StrEnum):
    #: Analizable para morfología. La Etapa 2 solo mira estos tramos.
    GOOD = "good"
    #: Sirve para contar latidos y medir R-R, no para comparar formas.
    MARGINAL = "marginal"
    #: No se puede afirmar nada. Es un dato clínico: el médico tiene que saber
    #: qué fracción del registro no se evaluó.
    BAD = "bad"
    UNKNOWN = "unknown"


class SignalQualityInterval(TimestampMixin, Base):
    __tablename__ = "signal_quality_interval"
    __table_args__ = (
        CheckConstraint("start_sample_index >= 0", name="ck_sqi_start"),
        CheckConstraint("sample_count > 0", name="ck_sqi_count"),
        CheckConstraint("window_count > 0", name="ck_sqi_windows"),
        Index("ix_sqi_batch", "batch_id"),
        # Cubre también el listado por estudio en orden de grabación: no hace
        # falta un índice aparte sobre las mismas columnas.
        Index(
            "uq_sqi_study_start",
            "study_id",
            "start_sample_index",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ecg_batch.id"), nullable=False
    )
    #: Absoluto al estudio, igual que `event_metadata["startSampleIndex"]`.
    start_sample_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sample_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    level: Mapped[SignalQualityLevel] = mapped_column(
        Enum(
            SignalQualityLevel,
            name="signal_quality_level",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    #: Qué capa lo degradó: `lead_off`, `saturated`, `firmware_sqi`, `flatline`,
    #: `psqi`, `ksqi`, `bassqi`, `no_beats`, `bsqi`, `ok`. Sin esto, "malo" no dice
    #: si el problema es el electrodo (se soluciona acomodando el chaleco) o la
    #: señal (no se soluciona). Las filas de antes de separar los índices
    #: espectrales traen `spectral`: texto libre, sin migración.
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    window_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Medianas de los índices sobre las ventanas fusionadas.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)

    study: Mapped[Study] = relationship()
    batch: Mapped[ECGBatch] = relationship()
