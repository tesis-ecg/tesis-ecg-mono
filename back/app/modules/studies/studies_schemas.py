import enum
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
    #: Cuando el registro es la respuesta del paciente al aviso de un hallazgo,
    #: el id de la anotación de ese hallazgo. El visor las dibuja unidas: sin
    #: esto, una respuesta y su taquicardia son dos marcas sueltas en la traza
    #: y el médico no tiene cómo saber cuál contesta a cuál. Va en `None`
    #: cuando el hallazgo no llegó a la señal (o el aviso no cuelga de uno).
    linkedAnnotationId: uuid.UUID | None = None
    #: Texto corto para el visor. Hoy solo lo llenan los registros del
    #: paciente, con los síntomas que informó.
    description: str | None = None


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
    #: Tipo del hallazgo que disparó el aviso respondido, ya resuelto contra el
    #: evento. `None` en los registros espontáneos.
    alertKind: str | None
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


class SimulatedAnomalyType(enum.StrEnum):
    """Hallazgos que el disparador manual sabe fabricar.

    Son los clínicos y no los de calidad de señal: el aviso al paciente existe
    para preguntarle cómo se sentía, y "el electrodo hizo ruido" no es una
    pregunta que él pueda contestar. Cada valor tiene ya su etiqueta en el
    portal (`features/alerts/labels.ts`) y en la app (`deviceMeta.ts`), así que
    ninguno aparece como "Hallazgo" genérico.
    """

    TACHYCARDIA = "tachycardia"
    BRADYCARDIA = "bradycardia"
    AFIB = "afib"
    PVC = "pvc"
    PAUSE = "pause"


class SimulateAnomalyRequest(CamelModel):
    """Lo que el simulador de chalecos manda para fabricar un hallazgo.

    El hallazgo se ancla **dentro de la señal ya ingerida** (`secondsBeforeEnd`
    desde el final de lo grabado) y no en el instante del pedido. Eso es lo que
    hace que el `occurredAt` del push caiga dentro de la grabación y que la
    respuesta del paciente se pueda pintar sobre el ECG sin esperar al lote
    siguiente.
    """

    eventType: SimulatedAnomalyType = SimulatedAnomalyType.AFIB
    #: Solo `high` y `critical`: son las que despiertan al celular
    #: (`notifications_service.PUSHABLE_SEVERITIES`). Una `low` no notificaría y
    #: el botón no haría nada visible, que es peor que no ofrecer la opción.
    severity: Literal["high", "critical"] = "high"
    durationSeconds: float = Field(default=8, gt=0, le=600)
    secondsBeforeEnd: float = Field(default=30, ge=0, le=86_400)
    message: str | None = Field(default=None, max_length=1024)


class SimulateAnomalyOut(CamelModel):
    alertId: uuid.UUID
    eventId: uuid.UUID
    #: Instante del hallazgo en hora de pared. Es el que viaja en el push y el
    #: que la app usa para anclar el formulario.
    occurredAt: datetime
    #: El mismo instante como offset desde el inicio de la grabación, que es la
    #: coordenada del gráfico del portal.
    offsetMs: int


@dataclass(frozen=True)
class SimulateAnomalyInput:
    doctor_id: uuid.UUID | None
    study_id: uuid.UUID
    actor_id: uuid.UUID | None
    data: SimulateAnomalyRequest


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


# --------------------------------------------------------------------------- #
# Hallazgos del motor de detección
# --------------------------------------------------------------------------- #


class StudyFindingOut(CamelModel):
    """Un hallazgo puntual, ubicable sobre la traza."""

    id: uuid.UUID
    kind: str
    category: Literal["signal_quality", "clinical", "patient_marker", "technical"]
    severity: Literal["low", "medium", "high", "critical"]
    startOffsetMs: int
    endOffsetMs: int
    #: Cuán atípico, en [0, 1]. **No es una probabilidad calibrada**: el motor es
    #: no supervisado y no hay con qué calibrarla.
    confidenceScore: float | None
    #: `null` en los hallazgos previos al motor y en los del disparador manual.
    #: Es lo que distingue "esto lo afirmó un modelo, y cuál" de "esto es un bit
    #: del hardware".
    modelVersion: str | None
    validationStatus: Literal["pending", "confirmed", "rejected", "uncertain"]
    clusterId: int | None = None
    beatCount: int | None = None
    description: str | None = None


class StudyFindingGroupOut(CamelModel):
    """Una morfología recurrente, o todos los hallazgos de un mismo tipo.

    Agrupar no es cosmético: 412 latidos de la misma forma son **un** hallazgo
    con 412 ocurrencias, no 412 filas. Sin esto, el panel del médico es
    ilegible y la herramienta no se usa.
    """

    key: str
    kind: str
    category: Literal["signal_quality", "clinical", "patient_marker", "technical"]
    #: La máxima del grupo.
    severity: Literal["low", "medium", "high", "critical"]
    occurrences: int
    beatCount: int | None = None
    #: Porcentaje de los latidos del estudio que tiene esta morfología. Solo en
    #: los grupos de cluster.
    burdenPct: float | None = None
    #: Correlación media de los miembros con su centroide. Alta = foco real;
    #: baja = bolsa de artefactos que casualmente se parecieron.
    meanIntraCorrelation: float | None = None
    firstOffsetMs: int
    lastOffsetMs: int
    items: list[StudyFindingOut] = Field(default_factory=list)


class StudyQualityIntervalOut(CamelModel):
    startOffsetMs: int
    endOffsetMs: int
    level: Literal["good", "marginal", "bad", "unknown"]
    #: `lead_off`, `saturated`, `flatline`, `spectral`, `bsqi`, `no_beats`, `ok`.
    #: Sin el motivo, "malo" no distingue el electrodo despegado —que el paciente
    #: puede acomodar— del ruido muscular, que no.
    reason: str


class StudyQualitySummaryOut(CamelModel):
    """Cuánto del registro se pudo evaluar. Es un dato clínico, no una métrica.

    Un informe que no dice qué fracción del Holter era ilegible está afirmando
    de más: "no se detectaron arritmias" sobre un registro 40 % inutilizable no
    significa lo mismo que sobre uno limpio.
    """

    analyzableRatio: float
    goodRatio: float
    marginalRatio: float
    badRatio: float
    evaluatedMs: int
    intervals: list[StudyQualityIntervalOut] = Field(default_factory=list)


class StudyFindingsOut(CamelModel):
    studyId: uuid.UUID
    sampleRate: int
    sampleCount: int
    durationMs: int
    modelVersion: str | None
    quality: StudyQualitySummaryOut
    groups: list[StudyFindingGroupOut] = Field(default_factory=list)
    #: Marcadores del paciente y detalles técnicos: no se agrupan porque cada uno
    #: es un hecho suelto.
    ungrouped: list[StudyFindingOut] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    #: Verdadero si el tope de revisión recortó hallazgos. Se expone porque un
    #: listado recortado en silencio se lee como "esto es todo lo que hay".
    truncated: bool = False


@dataclass
class StudyFindingsInput:
    doctor_id: uuid.UUID | None
    study_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    items_per_group: int = 10
