import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.db.models.study import StudyStatus
from app.modules._base_schema import CamelModel


class PatientStudyOut(CamelModel):
    id: uuid.UUID
    patientId: uuid.UUID
    startedAt: datetime
    endedAt: datetime | None
    durationHours: float | None
    status: StudyStatus
    deviceId: uuid.UUID
    samplesCount: int
    eventsCount: int


class PatientStudiesResponse(CamelModel):
    items: list[PatientStudyOut]
    total: int


class StudyDetailOut(CamelModel):
    id: uuid.UUID
    patientId: uuid.UUID
    patientName: str
    startedAt: datetime
    endedAt: datetime | None
    durationMs: int
    deviceSerial: str
    status: StudyStatus
    doctorId: uuid.UUID | None
    doctorName: str | None


class StudyListResponse(CamelModel):
    items: list[StudyDetailOut]
    total: int
    limit: int
    offset: int


class StudyEcgOut(CamelModel):
    url: str
    sampleRate: int
    startTimestamp: int
    durationMs: int
    sampleCount: int
    expiresAt: datetime


class StudyEcgObjectOut(CamelModel):
    url: str
    expiresAt: datetime
    byteLength: int
    sha256: str | None


class StudyEcgLevelOut(StudyEcgObjectOut):
    samplesPerBucket: int
    pointCount: int
    encoding: str = "minmax-float32-le"


class StudyEcgSegmentOut(StudyEcgObjectOut):
    """Un tramo de señal decodificada, tal como llegó en un lote del chaleco."""

    startSampleIndex: int
    sampleCount: int


class StudyEcgAnnotationOut(CamelModel):
    """Rango significativo dentro de la señal, normalizado para el visor."""

    id: uuid.UUID
    kind: str
    category: Literal["signal_quality", "clinical", "patient_marker", "technical"]
    severity: Literal["low", "medium", "high", "critical"]
    startOffsetMs: int
    endOffsetMs: int
    confidenceScore: float | None


class StudyPatientReportOut(CamelModel):
    """Un registro de la bitácora del paciente, visto por el médico.

    `offsetMs` es dónde cae dentro de la grabación y `visibleInChart` dice si
    ya hay señal ahí. Los dos pueden ser "todavía no": el paciente marca un
    síntoma en el momento y el chaleco sube esa hora hasta 60 minutos después.
    El registro existe desde el primer segundo; la banda sobre el ECG aparece
    cuando llega el lote.
    """

    id: uuid.UUID
    occurredAt: datetime
    source: Literal["push_response", "manual"]
    symptoms: list[str]
    #: Etiquetas ya resueltas contra el catálogo. Viajan desde el backend para
    #: que el portal no tenga que mantener una copia del catálogo que se
    #: desincronice cada vez que se agrega un síntoma.
    symptomLabels: list[str]
    symptomsOther: str | None
    activity: str
    activityLabel: str
    activityOther: str | None
    notes: str | None
    alertId: uuid.UUID | None
    createdAt: datetime
    offsetMs: int | None
    visibleInChart: bool


class StudyPatientReportsResponse(CamelModel):
    items: list[StudyPatientReportOut]
    total: int
    #: Cuántos todavía no tienen señal debajo. Es lo que el portal muestra
    #: agrupado aparte para que el médico no crea que se perdieron.
    pendingSignalTotal: int


class StudyEcgManifestOut(CamelModel):
    """Manifest v2.

    Dos formas de estudio conviven:

    - **Seedeado / legacy**: toda la señal en un solo objeto (`raw`), `segments`
      vacío. Es lo que produce `seed_demo`.
    - **Ingestado**: `raw` en `null` y la señal repartida en `segments`, uno por
      lote. S3 no soporta append, así que un blob único obligaría a reescribir
      173 MB cada hora.

    En los dos casos `levels` es lo que consume el visor para la vista general,
    así que el cliente casi nunca necesita mirar `raw` ni `segments`.
    """

    formatVersion: int = 2
    channel: str = "ecg"
    encoding: str
    sampleRate: int
    sampleCount: int
    startTimestamp: int
    durationMs: int
    status: StudyStatus
    isSimulated: bool
    raw: StudyEcgObjectOut | None
    levels: list[StudyEcgLevelOut]
    segments: list[StudyEcgSegmentOut] = Field(default_factory=list)
    annotations: list[StudyEcgAnnotationOut] = Field(default_factory=list)


@dataclass(frozen=True)
class StudyListInput:
    doctor_id: uuid.UUID | None
    q: str | None
    status: list[StudyStatus] | None
    limit: int
    offset: int


@dataclass(frozen=True)
class PatientStudiesInput:
    doctor_id: uuid.UUID | None
    patient_id: uuid.UUID


@dataclass(frozen=True)
class StudyIdInput:
    doctor_id: uuid.UUID | None
    study_id: uuid.UUID
    actor_id: uuid.UUID | None = None
