"""Tramos contiguos de un estudio, con su hora de pared real.

**Por qué existe.** El buffer de muestras de un estudio se arma pegando cada
lote al anterior (`ingest/processing`: `start_sample_index = study.samples_count`),
así que es continuo por construcción. La grabación no lo es: el chaleco se
reinicia, se queda sin batería, se sale del alcance del WiFi. Sin esta tabla el
índice de muestra se come el hueco y la hora que muestra el visor queda corrida
para todo lo que viene después — y nadie se entera.

Un tramo es una corrida máxima de tramas con `bootId` común y `t0Ms` monótono.
Se abre uno nuevo cuando pasa cualquiera de estas tres cosas (`INTEGRACION.md` §5):

1. cambia el `bootId` — reinicio, watchdog, cambio de batería;
2. `t0Ms` retrocede con el mismo `bootId` — wraparound de `millis()` a los 49,7 días;
3. el `t0Ms` salta hacia adelante más que la tolerancia — el equipo estuvo
   despierto y no grabó. Se mide en el reloj del equipo justamente para que la
   latencia del pedido, que vive en el ancla, no se cuele en la decisión.

Cada tramo lleva su propia ancla, así que un corte de energía en el medio de una
medición no arruina la hora de lo que se grabó después: el hueco queda con su
duración real y el tramo siguiente arranca en su hora correcta.
"""

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.study import Study


class TimeSyncSource(enum.StrEnum):
    """De dónde salió la hora de pared de un tramo.

    Vive acá y no en `dependencies/device_dependencies` —que es donde se leen
    las cabeceras— para no armar un ciclo de imports: la dependencia de auth
    del chaleco ya importa modelos, así que el enum tiene que estar del lado
    bajo de esa flecha.

    `NTP` es el camino normal: el ESP32-C3 sincronizó por SNTP en este ciclo de
    envío, o propagó una sincronización anterior con su propio reloj. `NONE`
    dice que SNTP falló y el valor es la mejor estimación disponible — se acepta
    igual y se archiva marcado, porque perder señal por no saber la hora sería
    mucho peor que archivarla con la hora aproximada. `SERVER_RECEIVE` no la
    manda nadie: es lo que escribimos cuando el ancla se derivó de nuestra
    propia hora de recepción, o sea el camino de antes de
    `docs/integracion-ingesta-con-horario.md`.
    """

    NTP = "ntp"
    NONE = "none"
    SERVER_RECEIVE = "server_receive"


class StudyTimelineSegment(TimestampMixin, Base):
    __tablename__ = "study_timeline_segment"
    __table_args__ = (
        UniqueConstraint("study_id", "ordinal", name="uq_timeline_study_ordinal"),
        CheckConstraint("sample_count >= 0", name="ck_timeline_sample_count"),
        CheckConstraint("start_sample_index >= 0", name="ck_timeline_start_sample"),
        CheckConstraint("end_epoch_ms >= start_epoch_ms", name="ck_timeline_epoch_range"),
        CheckConstraint("boot_id IS NULL OR boot_id BETWEEN 0 AND 15", name="ck_timeline_boot_id"),
        CheckConstraint(
            "anchor_uncertainty_ms IS NULL OR anchor_uncertainty_ms >= 0",
            name="ck_timeline_uncertainty",
        ),
        Index("ix_timeline_study_start_sample", "study_id", "start_sample_index"),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id"), nullable=False
    )
    #: Orden del tramo dentro del estudio, desde 0. Además de ordenar, es lo que
    #: prefija las claves de S3: sin él, dos tramos que arranquen en la misma
    #: `seq` (el `seq` rebobina al cambiar el formato de metadata de la flash,
    #: `INTEGRACION.md` §11.6) se pisarían el objeto en silencio.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    boot_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    first_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: Dónde cae este tramo dentro del buffer empaquetado del estudio. Es lo que
    #: convierte índice de muestra en hora de pared y viceversa.
    start_sample_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sample_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    #: Hora de pared de la primera y la última muestra del tramo.
    start_epoch_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_epoch_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: `t0Ms` de la primera trama del tramo. El ancla se guarda cruda además de
    #: la hora ya derivada: si más adelante resulta que estaba mal, con el crudo
    #: se puede recalcular todo; con solo el UTC derivado, no (`INTEGRACION.md` §5).
    first_t0_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_t0_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Ancla del tramo: instante UTC en que el `millis()` del equipo valía cero.
    boot_epoch_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Corrección de deriva del cristal, en partes por millón contra nuestro
    #: reloj. Un cristal de 20 ppm corre ~1,7 s por día, así que sin esto un
    #: estudio de 24 h no cierra en el objetivo de ±1 s.
    anchor_slope_ppm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    anchor_source: Mapped[TimeSyncSource] = mapped_column(
        Enum(
            TimeSyncSource, name="time_sync_source", values_callable=lambda e: [v.value for v in e]
        ),
        default=TimeSyncSource.SERVER_RECEIVE,
        nullable=False,
    )
    anchor_uncertainty_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    study: Mapped["Study"] = relationship()
