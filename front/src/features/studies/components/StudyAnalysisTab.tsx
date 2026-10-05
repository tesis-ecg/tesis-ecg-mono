import {
  Activity,
  Ban,
  CircleAlert,
  CircleCheck,
  CirclePause,
  Clock,
  Gauge,
  HeartPulse,
  ScanHeart,
  Timer,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  ANNOTATION_SEVERITY,
  annotationIcon,
  annotationLabel,
  formatAnnotationDuration,
} from '@/features/ecg/annotationMeta'
import { formatDate, formatDuration, formatMetricValue } from '@/features/ecg/clinicalReportFormat'
import { metricsStatusText } from '@/features/ecg/clinicalReportSummary'
import type { ECGAnnotation } from '@/features/ecg/types'
import { useHolterMetrics } from '@/features/studies/hooks/useHolterMetrics'
import { useStudyFindings } from '@/features/studies/hooks/useStudyFindings'
import type {
  HolterMetrics,
  MetricEvidence,
  Study,
  StudyFinding,
  StudyFindingGroup,
  StudyFindings,
  StudyQualitySummary,
} from '@/features/studies/types'
import { unwrapError } from '@/lib/api'
import { cn } from '@/lib/utils'

interface StudyAnalysisTabProps {
  study: Study
  /**
   * Lleva el visor al hallazgo o a la evidencia de una métrica. Recibe una
   * anotación con la hora de pared del tramo: es lo mismo que espera
   * `focusViewerOnAnnotation`, así que la página no necesita saber de dónde
   * salió el click.
   */
  onLocate: (annotation: ECGAnnotation) => void
}

/**
 * Lo que el sistema analizó del estudio, en tres bloques con dos dueños:
 *
 * - **Calidad** y **Hallazgos** salen del motor de detección (`/findings`).
 * - **Ritmo y variabilidad** sale de las métricas Holter (`/holter-metrics`), la
 *   misma fuente que congela el informe clínico. El motor también lleva sus
 *   propios totales de FC y VFC, pero no se muestran: dos SDNN distintos para el
 *   mismo estudio obligarían al médico a elegir cuál creer.
 *
 * No hay intervalos (QT/QTc) ni tendencias: el módulo de intervalos todavía es
 * experimental, y la tendencia horaria ya está en el informe.
 */
export function StudyAnalysisTab({ study, onLocate }: StudyAnalysisTabProps) {
  // Mientras el chaleco sigue mandando lotes, el análisis avanza con la
  // pantalla abierta: las dos consultas se refrescan con la cadencia del visor.
  const isInProgress = study.status === 'in_progress'
  const findings = useStudyFindings(study.id, isInProgress)
  const metrics = useHolterMetrics(study.id, isInProgress)

  // Con datos en mano, se muestran aunque el último refresco haya fallado: un
  // corte de red durante el sondeo no tiene por qué borrar lo que el médico
  // está leyendo. El error se muestra solo cuando no hay nada que mostrar.
  return (
    <div className="flex flex-col gap-4">
      {findings.data ? (
        <QualityCard findings={findings.data} isInProgress={isInProgress} />
      ) : findings.isLoading ? (
        <Card className="p-6">
          <Spinner label="Cargando calidad de la señal…" />
        </Card>
      ) : (
        // Calidad y hallazgos comparten consulta: un solo aviso con un solo
        // botón, y no dos tarjetas de error idénticas.
        <Card className="p-6">
          <EmptyState
            icon={Gauge}
            title="No pudimos cargar la calidad ni los hallazgos"
            description={
              findings.error
                ? unwrapError(findings.error)
                : 'El análisis del motor no está disponible.'
            }
            action={
              <Button variant="outline" onClick={() => void findings.refetch()}>
                Reintentar
              </Button>
            }
          />
        </Card>
      )}

      {metrics.data ? (
        <RhythmCard metrics={metrics.data} isInProgress={isInProgress} onLocate={onLocate} />
      ) : metrics.isLoading ? (
        <Card className="p-6">
          <Spinner label="Cargando métricas de ritmo…" />
        </Card>
      ) : (
        <Card className="p-6">
          <EmptyState
            icon={HeartPulse}
            title="No pudimos cargar las métricas de ritmo"
            description={
              metrics.error
                ? unwrapError(metrics.error)
                : 'Las métricas Holter no están disponibles.'
            }
            action={
              <Button variant="outline" onClick={() => void metrics.refetch()}>
                Reintentar
              </Button>
            }
          />
        </Card>
      )}

      {findings.data ? (
        <FindingsCard findings={findings.data} onLocate={onLocate} />
      ) : findings.isLoading ? (
        <Card className="p-6">
          <Spinner label="Cargando hallazgos…" />
        </Card>
      ) : null}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Calidad
// --------------------------------------------------------------------------- //

/**
 * Motivos del motor de calidad (`back/app/ml/contracts.py::QualityReason`).
 *
 * El motivo es lo que distingue un electrodo despegado —que el paciente puede
 * acomodar— del ruido muscular, que no. `spectral` es el motivo único que
 * usaban los estudios analizados antes de separar los tres índices espectrales;
 * se sigue nombrando para que esos estudios no muestren un código crudo.
 */
const QUALITY_REASON_LABEL: Record<string, string> = {
  lead_off: 'Electrodo desconectado',
  saturated: 'Saturación del ADC',
  firmware_sqi: 'Señal marcada como mala por el Holter',
  flatline: 'Señal plana o cortada',
  psqi: 'Energía fuera de la banda del QRS (pSQI)',
  ksqi: 'Señal sin picos definidos (kSQI)',
  bassqi: 'Deriva de la línea de base (basSQI)',
  spectral: 'Índices espectrales fuera de rango',
  no_beats: 'Sin latidos detectados',
  bsqi: 'Detectores de latidos en desacuerdo (bSQI)',
}

function qualityReasonLabel(reason: string): string {
  return QUALITY_REASON_LABEL[reason] ?? annotationLabel(reason)
}

/** Cuántos motivos se listan: más que eso deja de ser "los principales". */
const TOP_REASONS = 3

interface QualityReasonShare {
  reason: string
  ms: number
}

/**
 * Tiempo no bueno por motivo, de mayor a menor.
 *
 * Es aproximado a propósito: el backend funde tramos contiguos del mismo nivel
 * y se queda con el motivo del primero, así que un tramo malo que pasó de
 * `psqi` a `ksqi` cuenta entero como `psqi`. Alcanza para decir qué dominó.
 */
function topQualityReasons(quality: StudyQualitySummary): QualityReasonShare[] {
  const byReason = new Map<string, number>()
  for (const interval of quality.intervals ?? []) {
    if (interval.level === 'good') continue
    const ms = Math.max(interval.endOffsetMs - interval.startOffsetMs, 0)
    byReason.set(interval.reason, (byReason.get(interval.reason) ?? 0) + ms)
  }
  return [...byReason]
    .map(([reason, ms]) => ({ reason, ms }))
    .filter((share) => share.ms > 0)
    .sort((a, b) => b.ms - a.ms || a.reason.localeCompare(b.reason))
    .slice(0, TOP_REASONS)
}

function formatRatio(ratio: number): string {
  return formatMetricValue(ratio * 100, 1, '%')
}

function QualityCard({
  findings,
  isInProgress,
}: {
  findings: StudyFindings
  isInProgress: boolean
}) {
  const quality = findings.quality
  const description = isInProgress
    ? 'Evaluada por el motor de detección. Se actualiza a medida que llegan los lotes.'
    : 'Evaluada por el motor de detección sobre la señal recibida.'

  if (quality.evaluatedMs <= 0) {
    return (
      <Card className="flex flex-col gap-5 p-6">
        <SectionHeader icon={Gauge} title="Calidad de la señal" description={description} />
        <EmptyState
          icon={Gauge}
          title="Calidad todavía no evaluada"
          description="El motor de detección todavía no analizó señal de este estudio."
        />
      </Card>
    )
  }

  const reasons = topQualityReasons(quality)

  return (
    <Card className="flex flex-col gap-5 p-6">
      <SectionHeader icon={Gauge} title="Calidad de la señal" description={description} />

      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <AnalysisMetric
          icon={CircleCheck}
          label="Analizable"
          value={formatRatio(quality.analyzableRatio)}
          hint={formatDuration(quality.analyzableRatio * quality.evaluatedMs)}
        />
        <AnalysisMetric
          icon={CircleAlert}
          label="Marginal"
          value={formatRatio(quality.marginalRatio)}
          hint={formatDuration(quality.marginalRatio * quality.evaluatedMs)}
        />
        <AnalysisMetric
          icon={TriangleAlert}
          label="No analizable"
          value={formatRatio(quality.badRatio)}
          hint={formatDuration(quality.badRatio * quality.evaluatedMs)}
        />
        <AnalysisMetric
          icon={Clock}
          label="Tiempo evaluado"
          value={formatDuration(quality.evaluatedMs)}
          hint={`de ${formatDuration(findings.durationMs)} grabados`}
        />
      </dl>

      {reasons.length > 0 && (
        <section aria-label="Motivos principales" className="flex flex-col gap-2">
          <h3 className="text-body3 font-medium tracking-wide text-gray-500 uppercase">
            Motivos principales
          </h3>
          <ul className="flex flex-col gap-1.5">
            {reasons.map((share) => (
              <li
                key={share.reason}
                className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5"
              >
                <span className="text-body2 text-gray-900">{qualityReasonLabel(share.reason)}</span>
                <span className="text-body3 text-gray-600">
                  {formatRatio(share.ms / quality.evaluatedMs)} · {formatDuration(share.ms)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Ritmo y variabilidad
// --------------------------------------------------------------------------- //

type EvidenceKind = 'hr_min' | 'hr_max' | 'pause_longest'

/**
 * La evidencia de una métrica como anotación, para reusar el mismo salto que
 * un hallazgo. El `kind` es el de las tiras del informe, así que el rótulo
 * ("FC mínima", "Pausa más larga") sale del mismo catálogo.
 */
function evidenceAnnotation(kind: EvidenceKind, evidence: MetricEvidence): ECGAnnotation {
  return {
    id: `metric:${kind}`,
    kind,
    category: 'clinical',
    severity: 'low',
    startMs: evidence.epochMs,
    endMs: evidence.epochMs + (evidence.durationMs ?? 0),
    confidenceScore: null,
    linkedAnnotationId: null,
    description: null,
  }
}

function RhythmCard({
  metrics,
  isInProgress,
  onLocate,
}: {
  metrics: HolterMetrics
  isInProgress: boolean
  onLocate: (annotation: ECGAnnotation) => void
}) {
  const header = (
    <SectionHeader
      icon={HeartPulse}
      title="Ritmo y variabilidad"
      description="Las mismas métricas que congela el informe clínico."
    />
  )

  if (metrics.status !== 'ok') {
    return (
      <Card className="flex flex-col gap-5 p-6">
        {header}
        <EmptyState
          icon={HeartPulse}
          title="Métricas de ritmo no disponibles"
          description={[
            metricsStatusText(metrics),
            isInProgress ? 'Se calculan a medida que llegan los lotes del Holter.' : null,
          ]
            .filter(Boolean)
            .join(' ')}
        />
      </Card>
    )
  }

  const heart = metrics.heartRate
  const pauses = metrics.pauses
  const time = metrics.hrvTime
  const analysis = metrics.analysis

  const locate = (kind: EvidenceKind, evidence: MetricEvidence | null | undefined) =>
    evidence ? (
      <Button
        variant="ghost"
        size="sm"
        className="-ml-3"
        aria-label={`Ver ${annotationLabel(kind)} en el ECG`}
        onClick={() => onLocate(evidenceAnnotation(kind, evidence))}
      >
        Ver en el ECG
      </Button>
    ) : undefined

  return (
    <Card className="flex flex-col gap-5 p-6">
      {header}

      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <AnalysisMetric
          icon={HeartPulse}
          label="FC promedio"
          value={formatMetricValue(heart?.averageBpm, 0, 'lpm')}
          hint={heart ? `${formatMetricValue(heart.totalBeats)} latidos` : undefined}
        />
        <AnalysisMetric
          icon={TrendingDown}
          label="FC mínima"
          value={formatMetricValue(heart?.min?.value, 0, 'lpm')}
          hint={heart?.min ? formatDate(heart.min.epochMs) : undefined}
          action={locate('hr_min', heart?.min)}
        />
        <AnalysisMetric
          icon={TrendingUp}
          label="FC máxima"
          value={formatMetricValue(heart?.max?.value, 0, 'lpm')}
          hint={heart?.max ? formatDate(heart.max.epochMs) : undefined}
          action={locate('hr_max', heart?.max)}
        />
        <AnalysisMetric
          icon={CirclePause}
          label={`Pausas R-R > ${pauses?.thresholdMs ?? 2000} ms`}
          value={formatMetricValue(pauses?.count)}
        />
        <AnalysisMetric
          icon={Timer}
          label="Pausa más larga"
          value={
            pauses?.longest
              ? formatMetricValue((pauses.longest.durationMs ?? 0) / 1000, 2, 's')
              : '—'
          }
          hint={pauses?.longest ? formatDate(pauses.longest.epochMs) : undefined}
          action={locate('pause_longest', pauses?.longest)}
        />
        <AnalysisMetric
          icon={Activity}
          label="SDNN"
          value={formatMetricValue(time?.sdnnMs, 1, 'ms')}
        />
        <AnalysisMetric
          icon={Activity}
          label="rMSSD"
          value={formatMetricValue(time?.rmssdMs, 1, 'ms')}
        />
        <AnalysisMetric
          icon={Activity}
          label="pNN50"
          value={formatMetricValue(time?.pnn50Percent, 1, '%')}
        />
        <AnalysisMetric
          icon={Clock}
          label="Tiempo analizado"
          value={analysis ? formatDuration(analysis.analyzedMs) : formatMetricValue(null)}
          hint={isInProgress ? 'Análisis en curso' : undefined}
        />
        <AnalysisMetric
          icon={Ban}
          label="Excluido por calidad"
          value={analysis ? formatDuration(analysis.excludedMs) : formatMetricValue(null)}
        />
      </dl>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Hallazgos
// --------------------------------------------------------------------------- //

function findingAnnotation(finding: StudyFinding): ECGAnnotation {
  return {
    id: finding.id,
    kind: finding.kind,
    category: finding.category,
    severity: finding.severity,
    startMs: finding.startEpochMs,
    endMs: finding.endEpochMs,
    confidenceScore: finding.confidenceScore,
    linkedAnnotationId: null,
    description: finding.description ?? null,
  }
}

/**
 * A dónde lleva el click en el encabezado de un grupo: su primer episodio, el que
 * abre el rango que muestra el encabezado.
 *
 * Una morfología recurrente sin episodios listados abarca del primer al último
 * latido de su cluster —horas—, y centrar el visor en la mitad de eso no
 * mostraría nada. Se va al primer latido, como un instante. Lo mismo un grupo
 * recortado: el backend lista los episodios más atípicos, no los primeros, y el
 * primero de la lista puede ser horas posterior a la hora que dice el encabezado.
 */
function groupAnnotation(group: StudyFindingGroup): ECGAnnotation {
  const first = group.items?.[0]
  if (first && first.startEpochMs === group.firstEpochMs) return findingAnnotation(first)
  return {
    id: group.key,
    kind: group.kind,
    category: group.category,
    severity: group.severity,
    startMs: group.firstEpochMs,
    endMs: group.firstEpochMs,
    confidenceScore: null,
    linkedAnnotationId: null,
    description: null,
  }
}

function plural(count: number, singular: string, pluralForm: string): string {
  return `${formatMetricValue(count)} ${count === 1 ? singular : pluralForm}`
}

/** "12 episodios · 1.204 latidos · carga 3,2 % · morfología n.º 3". */
function groupSummary(group: StudyFindingGroup): string {
  const parts: string[] = []
  if (group.occurrences > 0) parts.push(plural(group.occurrences, 'episodio', 'episodios'))
  if (group.beatCount != null) parts.push(plural(group.beatCount, 'latido', 'latidos'))
  if (group.burdenPct != null) parts.push(`carga ${formatMetricValue(group.burdenPct, 1, '%')}`)
  if (group.key.startsWith('cluster:')) parts.push(`morfología n.º ${group.key.slice(8)}`)
  return parts.join(' · ')
}

function FindingsCard({
  findings,
  onLocate,
}: {
  findings: StudyFindings
  onLocate: (annotation: ECGAnnotation) => void
}) {
  const groups = findings.groups ?? []
  const ungrouped = findings.ungrouped ?? []

  return (
    <Card className="flex flex-col gap-5 p-6">
      <SectionHeader
        icon={ScanHeart}
        title="Hallazgos"
        description="Soporte a la decisión — no diagnóstico"
        aside={
          findings.modelVersion ? (
            <span className="text-body3 text-gray-500">Motor {findings.modelVersion}</span>
          ) : undefined
        }
      />

      {groups.length === 0 && ungrouped.length === 0 ? (
        <EmptyState
          icon={ScanHeart}
          title="Sin hallazgos"
          description={
            findings.quality.evaluatedMs > 0
              ? 'El motor de detección no registró hallazgos en la señal evaluada.'
              : 'El motor de detección todavía no analizó señal de este estudio.'
          }
        />
      ) : (
        <>
          {/* Un listado recortado en silencio se lee como "esto es todo lo que
              hay": el conteo del grupo es el total, la lista no. */}
          {findings.truncated && (
            <p className="text-body3 rounded-lg border border-info-300 bg-info-100 px-4 py-3 text-info-700">
              Cada grupo lista sus episodios más atípicos; el conteo del grupo es el total.
            </p>
          )}

          {groups.length > 0 && (
            <ul className="flex flex-col gap-2" aria-label="Hallazgos agrupados">
              {groups.map((group) => (
                <FindingGroupRow key={group.key} group={group} onLocate={onLocate} />
              ))}
            </ul>
          )}

          {ungrouped.length > 0 && (
            <section aria-label="Eventos puntuales" className="flex flex-col gap-2">
              <h3 className="text-body3 font-medium tracking-wide text-gray-500 uppercase">
                Eventos puntuales
              </h3>
              <ul className="flex flex-col gap-1">
                {ungrouped.map((finding) => (
                  <li key={finding.id}>
                    <FindingItemButton finding={finding} onLocate={onLocate} showLabel />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Card>
  )
}

function FindingGroupRow({
  group,
  onLocate,
}: {
  group: StudyFindingGroup
  onLocate: (annotation: ECGAnnotation) => void
}) {
  const severity = ANNOTATION_SEVERITY[group.severity]
  const items = group.items ?? []
  const summary = groupSummary(group)

  return (
    <li className="rounded-md border border-border bg-card p-1" data-finding-group={group.key}>
      <button
        type="button"
        onClick={() => onLocate(groupAnnotation(group))}
        className={cn(
          'w-full cursor-pointer rounded-sm p-2 text-left transition-colors hover:bg-gray-50',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
        )}
      >
        <div className="flex items-start gap-2.5">
          <SeverityIcon icon={annotationIcon(group.category)} severity={group.severity} />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center justify-between gap-1">
              <span className="text-body2 font-medium text-gray-900">
                {annotationLabel(group.kind)}
              </span>
              <Badge variant={severity.badgeVariant}>{severity.label}</Badge>
            </span>
            {summary && <span className="text-body3 mt-1 block text-gray-600">{summary}</span>}
            <span className="text-body3 mt-0.5 block text-gray-500">
              {group.firstEpochMs === group.lastEpochMs
                ? formatDate(group.firstEpochMs)
                : `${formatDate(group.firstEpochMs)} – ${formatDate(group.lastEpochMs)}`}
            </span>
          </span>
        </div>
      </button>

      {items.length > 0 && (
        <ul className="ml-5 border-l-2 border-primary-100 pl-2.5">
          {items.map((item) => (
            <li key={item.id}>
              <FindingItemButton finding={item} onLocate={onLocate} />
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

function FindingItemButton({
  finding,
  onLocate,
  showLabel = false,
}: {
  finding: StudyFinding
  onLocate: (annotation: ECGAnnotation) => void
  /** Fuera de un grupo no hay encabezado que diga qué es. */
  showLabel?: boolean
}) {
  const label = annotationLabel(finding.kind)
  const when = formatDate(finding.startEpochMs)
  return (
    <button
      type="button"
      aria-label={`Ver ${label} del ${when} en el ECG`}
      onClick={() => onLocate(findingAnnotation(finding))}
      className={cn(
        'w-full cursor-pointer rounded-sm p-2 text-left transition-colors hover:bg-gray-50',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
      )}
    >
      {showLabel && <span className="text-body2 block font-medium text-gray-900">{label}</span>}
      <span className="text-body3 block text-gray-600">
        {when} · {formatAnnotationDuration(finding.startEpochMs, finding.endEpochMs)}
        {finding.confidenceScore != null &&
          ` · confianza ${Math.round(finding.confidenceScore * 100)}%`}
      </span>
    </button>
  )
}

// --------------------------------------------------------------------------- //
// Piezas compartidas — mismas que `StudyDeviceTab`
// --------------------------------------------------------------------------- //

/** El ícono de un hallazgo con el color de su severidad, como en el panel del visor. */
function SeverityIcon({
  icon: Icon,
  severity,
}: {
  icon: LucideIcon
  severity: StudyFindingGroup['severity']
}) {
  return (
    <span
      className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full"
      style={{
        color: `var(--ecg-alert-${severity})`,
        backgroundColor: `var(--ecg-alert-${severity}-bg)`,
      }}
    >
      <Icon className="size-4" aria-hidden />
    </span>
  )
}

interface SectionHeaderProps {
  icon: LucideIcon
  title: string
  description: string
  aside?: ReactNode
}

function SectionHeader({ icon: Icon, title, description, aside }: SectionHeaderProps) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
          <Icon className="size-5" aria-hidden />
        </div>
        <div>
          <h2 className="text-h6 text-gray-900">{title}</h2>
          <p className="text-body3 text-gray-600">{description}</p>
        </div>
      </div>
      {aside}
    </header>
  )
}

interface AnalysisMetricProps {
  icon: LucideIcon
  label: string
  value: string
  hint?: string
  action?: ReactNode
}

function AnalysisMetric({ icon: Icon, label, value, hint, action }: AnalysisMetricProps) {
  return (
    <div className="flex items-start gap-2.5">
      <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-primary-50 text-primary-500">
        <Icon className="size-4" aria-hidden />
      </div>
      <div>
        <dt className="text-body3 text-gray-600">{label}</dt>
        <dd className="text-body2 font-medium text-gray-900">{value}</dd>
        {hint && <dd className="text-helper text-gray-500">{hint}</dd>}
        {action && <dd>{action}</dd>}
      </div>
    </div>
  )
}
