import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.db.models.study import StudyStatus
from app.modules._base_schema import CamelModel


class PatientStudyOut(CamelModel):
    id: uuid.UUID
    patientId: uuid.UUID
    startedAt: datetime
    startedAtVerified: bool
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
    deviceId: uuid.UUID
    startedAt: datetime
    startedAtVerified: bool = True
    endedAt: datetime | None
    durationMs: int
    deviceSerial: str
    #: Indica si quien consulta todavía puede abrir el detalle y la telemetría
    #: actual del Holter. Un estudio histórico sigue siendo visible aunque el
    #: equipo haya sido transferido a otro médico.
    canAccessDevice: bool
    #: Último lote ECG recibido para este estudio. No usa el cache global del
    #: paciente, que podría pertenecer a un estudio posterior.
    lastDataReceivedAt: datetime | None
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


class StudyEcgLevelChunkOut(StudyEcgObjectOut):
    """Un tramo de un nivel, aportado por un lote."""

    pointCount: int


class StudyEcgLevelOut(CamelModel):
    """Un nivel de la pirámide, repartido en chunks.

    Antes era un objeto único que se reescribía entero en cada lote. Eso crecía
    con el estudio y corría con la fila del estudio bloqueada, una fuente de
    contención bajo ingesta sostenida. Ahora cada lote anexa lo suyo y
    los chunks se compactan de a ratos, así que el trabajo por lote es constante.

    El cliente concatena `chunks` en orden — es la misma mecánica que ya usa con
    `segments`. Un nivel compactado tiene un solo chunk.
    """

    samplesPerBucket: int
    pointCount: int
    encoding: str = "minmax-float32-le"
    chunks: list[StudyEcgLevelChunkOut]


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
    #: Hora de pared real del aviso, resuelta contra la línea de tiempo. Es lo
    #: que el visor pinta en el eje: los offsets son sobre el buffer empaquetado
    #: y se despegan de la hora en cuanto el estudio tiene un hueco.
    startEpochMs: int
    endEpochMs: int
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


class StudyEcgTimelineSegmentOut(CamelModel):
    """Una corrida contigua de grabación, con su hora de pared real.

    El buffer de muestras del estudio es continuo por construcción: cada lote se
    pega al anterior. La grabación no lo es. Estos tramos son la traducción entre
    las dos cosas, y son lo que permite dibujar un hueco como hueco en vez de
    pegar los bordes y correr la hora de todo lo que sigue.
    """

    ordinal: int
    startSampleIndex: int
    sampleCount: int
    startEpochMs: int
    endEpochMs: int
    bootId: int | None
    #: `ntp` es una hora sincronizada por el puente; `server_receive` es el
    #: camino viejo, derivado de nuestra hora de recepción y por lo tanto con la
    #: latencia del pedido adentro. El visor lo usa para avisar cuánto vale.
    anchorSource: Literal["ntp", "none", "server_receive"]
    anchorUncertaintyMs: int | None
    anchorMatchesBoot: bool | None = None


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

    formatVersion: int = 3
    channel: str = "ecg"
    encoding: str
    sampleRate: int
    sampleCount: int
    startTimestamp: int
    startTimeVerified: bool = True
    durationMs: int
    status: StudyStatus
    isSimulated: bool
    viewKind: Literal["raw", "filtered_visualization"] = "raw"
    raw: StudyEcgObjectOut | None
    levels: list[StudyEcgLevelOut]
    segments: list[StudyEcgSegmentOut] = Field(default_factory=list)
    #: Tramos contiguos con su hora de pared. Vacío en los estudios seedeados o
    #: legacy, donde el eje relativo sigue siendo correcto porque no hay huecos.
    timeline: list[StudyEcgTimelineSegmentOut] = Field(default_factory=list)
    annotations: list[StudyEcgAnnotationOut] = Field(default_factory=list)


class StudyEcgReportWindowRequest(CamelModel):
    """Ventana corta en hora de pared para una tira detallada del informe."""

    id: str = Field(min_length=1, max_length=120)
    startEpochMs: int = Field(ge=0)
    endEpochMs: int = Field(ge=0)


class StudyEcgReportWindowsRequest(CamelModel):
    """El límite mantiene acotado el JSON y permite al cliente paginar lotes."""

    windows: list[StudyEcgReportWindowRequest] = Field(min_length=1, max_length=25)


class StudyEcgReportWindowOut(CamelModel):
    id: str
    startEpochMs: int
    endEpochMs: int
    timestampsMs: list[int]
    samplesMv: list[float]
    gapIndices: list[int]
    #: `raw` hoy es el camino normal. Se deja explícito para que la UI nunca
    #: presente una envolvente futura como si fuera la señal cruda.
    source: Literal["raw", "envelope", "filtered_visualization"] = "raw"


class StudyEcgReportWindowsResponse(CamelModel):
    windows: list[StudyEcgReportWindowOut]


class StudyClinicalReportDraftUpdate(CamelModel):
    revision: int = Field(ge=0)
    indication: str | None = Field(default=None, max_length=4000)
    medications: str | None = Field(default=None, max_length=8000)
    referringProfessional: str | None = Field(default=None, max_length=240)
    technician: str | None = Field(default=None, max_length=240)
    clinicalObservations: str | None = Field(default=None, max_length=8000)
    conclusion: str | None = Field(default=None, max_length=12000)


class StudyClinicalReportDraftOut(CamelModel):
    studyId: uuid.UUID
    revision: int
    indication: str | None
    medications: str | None
    referringProfessional: str | None
    technician: str | None
    clinicalObservations: str | None
    conclusion: str | None
    updatedAt: datetime | None
    updatedBy: uuid.UUID | None
    updatedByName: str | None
    updatedByRole: str | None


class StudyClinicalReportWindowPlanOut(CamelModel):
    id: str
    #: `None` en las tiras de evidencia de una métrica (FC mínima, pausa más
    #: larga…), que no salen de un hallazgo sino del análisis de latidos.
    findingId: uuid.UUID | None
    kind: str
    category: Literal["clinical", "patient_marker", "metric"]
    severity: Literal["low", "medium", "high", "critical"]
    findingStartEpochMs: int
    findingEndEpochMs: int
    findingDurationMs: int
    startEpochMs: int
    endEpochMs: int
    blockIndex: int
    blockCount: int
    confidenceScore: float | None
    description: str | None
    relatedSymptoms: list[str]


class MetricEvidenceOut(CamelModel):
    """Una estadística con el instante que la respalda (req. 5, «evidencia»).

    `sampleIndex` es la coordenada del buffer empaquetado y `epochMs` la hora de
    pared; con cualquiera de las dos el visor y el informe ubican la tira de ECG.
    """

    value: float | None
    sampleIndex: int
    epochMs: int
    durationMs: int | None = None


class HolterAnalysisOut(CamelModel):
    algorithmVersion: int
    #: Hasta qué muestra se buscaron latidos. Con el estudio abierto queda un
    #: poco detrás del total: la cola del tramo activo espera contexto.
    analyzedUntilSample: int
    #: Tiempo analizable: grabado y sin tramos excluidos por calidad.
    analyzedMs: int
    excludedMs: int
    rrIntervals: int
    nnIntervals: int


class HolterHeartRateOut(CamelModel):
    averageBpm: float | None
    min: MetricEvidenceOut | None
    max: MetricEvidenceOut | None
    totalBeats: int
    #: `None` mientras no exista clasificación de latidos.
    abnormalBeats: int | None
    abnormalPerThousand: float | None
    #: FC mínima y máxima salen del promedio móvil de esta cantidad de NN.
    windowBeats: int


class HolterPausesOut(CamelModel):
    thresholdMs: int
    count: int
    longest: MetricEvidenceOut | None
    items: list[MetricEvidenceOut]


class HolterEctopyCountOut(CamelModel):
    episodes: int
    beats: int


class HolterEctopyOut(CamelModel):
    """Contrato de los recuadros S y V. Hoy siempre llega `None`: requiere el
    motor de clasificación de latidos (req. 3)."""

    total: int
    single: int
    pairs: HolterEctopyCountOut
    bigeminy: HolterEctopyCountOut
    trigeminy: HolterEctopyCountOut
    runs: HolterEctopyCountOut
    perThousand: float
    maxPerMinute: MetricEvidenceOut | None


class HolterHrvTimeOut(CamelModel):
    sdnnMs: float | None
    sdannMs: float | None
    rmssdMs: float | None
    pnn50Percent: float | None
    cv: float | None
    meanNnMs: float | None


class HolterSpectrumOut(CamelModel):
    frequenciesHz: list[float]
    powerMs2PerHz: list[float]


class HolterHrvFrequencyOut(CamelModel):
    #: «Energía» del informe: ULF + VLF + LF + HF.
    totalPowerMs2: float | None
    ulfMs2: float | None
    vlfMs2: float | None
    lfMs2: float | None
    hfMs2: float | None
    lfHfRatio: float | None
    windows: int
    spectrum: HolterSpectrumOut


class HolterStKindOut(CamelModel):
    episodes: int
    durationSeconds: int
    #: Magnitud en mV (positiva también para la depresión).
    maxDeviation: MetricEvidenceOut | None
    maxSlopeMvPerMin: float | None


class HolterStChannelOut(CamelModel):
    channel: int
    label: str
    analyzedMinutes: int
    medianLevelMv: float | None
    elevation: HolterStKindOut
    depression: HolterStKindOut


class HolterHourOut(CamelModel):
    hourStartEpochMs: int
    beats: int
    avgBpm: float | None
    minBpm: float | None
    maxBpm: float | None


class HolterRrHistogramOut(CamelModel):
    startMs: int
    binMs: int
    counts: list[int]


class HolterMetricsOut(CamelModel):
    """Métricas del informe Holter estándar (`Requerimientos.md` §4).

    `status`:
    - `ok`: calculadas sobre lo analizado hasta `analysis.analyzedUntilSample`.
    - `pending`: el estudio tiene señal pero todavía no se buscaron latidos
      (estudio previo a esta función: corre el backfill).
    - `insufficient_data`: no hay latidos suficientes para medir nada.
    - `unavailable`: el estudio no tiene señal segmentada que analizar.
    """

    status: Literal["ok", "pending", "insufficient_data", "unavailable"]
    unavailableReason: str | None
    analysis: HolterAnalysisOut | None
    heartRate: HolterHeartRateOut | None
    pauses: HolterPausesOut | None
    supraventricular: HolterEctopyOut | None
    ventricular: HolterEctopyOut | None
    ectopyUnavailableReason: str | None
    hrvTime: HolterHrvTimeOut | None
    hrvFrequency: HolterHrvFrequencyOut | None
    st: list[HolterStChannelOut]
    hourly: list[HolterHourOut]
    rrHistogram: HolterRrHistogramOut | None


class StudyClinicalReportIssueOut(CamelModel):
    code: str
    message: str
    severity: Literal["warning", "blocking"]


class StudyClinicalReportPreviewOut(CamelModel):
    draft: StudyClinicalReportDraftOut
    snapshot: dict[str, Any]
    snapshotHash: str
    windows: list[StudyClinicalReportWindowPlanOut]
    nextVersion: int
    canGenerateDraft: bool
    canFinalize: bool
    blockingReasons: list[str]
    issues: list[StudyClinicalReportIssueOut]


class StudyClinicalReportVersionOut(CamelModel):
    id: uuid.UUID
    studyId: uuid.UUID
    version: int
    finalizedAt: datetime
    finalizedBy: uuid.UUID
    finalizedByName: str
    finalizedByRole: str
    pdfByteLength: int
    pdfSha256: str
    snapshotSha256: str


class StudyClinicalReportVersionsOut(CamelModel):
    items: list[StudyClinicalReportVersionOut]


@dataclass(frozen=True)
class StudyClinicalReportDraftInput:
    doctor_id: uuid.UUID | None
    study_id: uuid.UUID
    actor_id: uuid.UUID
    data: StudyClinicalReportDraftUpdate


@dataclass(frozen=True)
class StudyClinicalReportFinalizeInput:
    doctor_id: uuid.UUID | None
    study_id: uuid.UUID
    actor_id: uuid.UUID
    draft_revision: int
    snapshot_hash: str
    pdf: bytes


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
    #: Hora de pared real, resuelta igual que en `StudyEcgAnnotationOut`: es lo
    #: que el panel usa para llevar el visor a la banda del hallazgo.
    startEpochMs: int
    endEpochMs: int
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
    firstEpochMs: int
    lastEpochMs: int
    items: list[StudyFindingOut] = Field(default_factory=list)


class StudyQualityIntervalOut(CamelModel):
    startOffsetMs: int
    endOffsetMs: int
    #: Hora de pared, igual que los hallazgos: los offsets son del buffer
    #: empaquetado y se despegan de la hora en cuanto el estudio tiene un hueco.
    #: Un intervalo nunca cruza una corrida, así que sus dos bordes se resuelven
    #: contra el mismo tramo.
    startEpochMs: int
    endEpochMs: int
    level: Literal["good", "marginal", "bad", "unknown"]
    #: `lead_off`, `saturated`, `firmware_sqi`, `flatline`, `psqi`, `ksqi`,
    #: `bassqi`, `no_beats`, `bsqi`, `ok`; `spectral` en estudios analizados antes
    #: de separar los tres índices espectrales.
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
