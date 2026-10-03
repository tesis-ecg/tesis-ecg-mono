import { jsPDF } from 'jspdf'

import type {
  HolterMetrics,
  StudyClinicalReportFindingSummary,
  StudyClinicalReportWindowPlan,
} from '@/features/studies/types'

import { annotationLabel } from './annotationMeta'
import type { EcgReportWindow } from './api/ecgApi'
import {
  clamp,
  CONTENT_WIDTH,
  formatAxisTime,
  formatDate,
  formatDuration,
  formatMetricValue,
  GRID,
  INK,
  MARGIN,
  PAGE_HEIGHT,
  PAGE_WIDTH,
} from './clinicalReportFormat'
import { drawSummaryPage, drawTrendsPage, hasTrends } from './clinicalReportSummary'
import type { ClinicalReportInput } from './clinicalReportTypes'
import type { ECGAnnotation } from './types'

export function buildClinicalReport(input: ClinicalReportInput): ArrayBuffer {
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4', compress: true })
  const { snapshot } = input
  let y = 27

  const header = () => {
    doc.setFillColor(...INK)
    doc.roundedRect(MARGIN, 8, CONTENT_WIDTH, 11, 1.5, 1.5, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(10)
    doc.setTextColor(255)
    doc.text('HOLTER ECG · INFORME CLÍNICO', MARGIN + 4, 15)
    doc.text(input.documentStatus === 'final' ? 'FINAL' : 'BORRADOR', PAGE_WIDTH - MARGIN - 4, 15, {
      align: 'right',
    })
    doc.setTextColor(0)
    y = 27
  }
  const nextPage = () => {
    doc.addPage()
    header()
  }
  const ensure = (height: number) => {
    if (y + height > PAGE_HEIGHT - 14) nextPage()
  }
  const title = (value: string) => {
    ensure(12)
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(15)
    doc.setTextColor(...INK)
    doc.text(value, MARGIN, y)
    doc.setTextColor(0)
    y += 9
  }
  const heading = (value: string) => {
    ensure(12)
    doc.setFillColor(...INK)
    doc.roundedRect(MARGIN, y - 4.5, CONTENT_WIDTH, 7, 1, 1, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(10.5)
    doc.setTextColor(255)
    doc.text(value, MARGIN + 3, y)
    doc.setTextColor(0)
    doc.setFont('helvetica', 'normal')
    y += 8
  }
  const row = (label: string, value: string | null | undefined) => {
    const lines = doc.splitTextToSize(value || '—', CONTENT_WIDTH - 48)
    const height = Math.max(6, lines.length * 4.2 + 2)
    ensure(height + 1)
    doc.setFillColor(247, 249, 252)
    doc.roundedRect(MARGIN, y - 3.8, CONTENT_WIDTH, height, 0.8, 0.8, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(8.5)
    doc.setTextColor(...INK)
    doc.text(label, MARGIN + 2, y)
    doc.setFont('helvetica', 'normal')
    doc.setTextColor(0)
    doc.text(lines, MARGIN + 46, y)
    y += height + 1
  }

  header()
  drawSummaryPage(doc, input, 28)
  if (hasTrends(snapshot.metrics)) {
    nextPage()
    drawTrendsPage(doc, snapshot.metrics, y)
  }

  nextPage()
  title('Detalle del estudio')
  row(
    'Estado / versión',
    `${input.documentStatus === 'final' ? 'FINAL' : 'BORRADOR'} · v${snapshot.version}`,
  )
  row('Generado', formatDate(input.generatedAt))
  row('Estudio', snapshot.study.id)
  row(
    'Médico responsable',
    [
      snapshot.responsibleDoctor.fullName,
      snapshot.responsibleDoctor.specialty,
      snapshot.responsibleDoctor.licenseNumber,
    ]
      .filter(Boolean)
      .join(' · '),
  )

  heading('Contexto clínico')
  row('Indicación', snapshot.clinicalContext.indication)
  row('Medicación', snapshot.clinicalContext.medications)
  row('Profesional derivante', snapshot.clinicalContext.referringProfessional)
  row('Técnico responsable', snapshot.clinicalContext.technician)
  row('Observaciones clínicas', snapshot.clinicalContext.clinicalObservations)

  heading('Calidad de adquisición')
  if (snapshot.quality.timeVerified === false) {
    row(
      'Hora de muestras',
      'No verificada: las fechas de hallazgos y tiras son estimaciones y no deben usarse como hora clínica.',
    )
  }
  row(
    'Tiempo grabado / lapso',
    `${formatDuration(snapshot.quality.recordedMs)} / ${formatDuration(snapshot.quality.wallClockMs)}`,
  )
  row(
    'Cobertura / interrupciones',
    `${snapshot.quality.coveragePercent.toFixed(1)} % / ${formatDuration(snapshot.quality.interruptionMs)}`,
  )
  row('Segmentos / cortes', `${snapshot.quality.segments} / ${snapshot.quality.cuts}`)
  row(
    'Sincronización',
    `${snapshot.quality.synchronizationSources.join(', ') || 'No disponible'} · incertidumbre máxima ${snapshot.quality.maxSynchronizationUncertaintyMs == null ? 'no determinada' : `${snapshot.quality.maxSynchronizationUncertaintyMs} ms`}`,
  )
  row(
    'Última recepción',
    snapshot.quality.lastDataReceivedAt ? formatDate(snapshot.quality.lastDataReceivedAt) : null,
  )
  row('Eventos técnicos y de calidad', summaryText(snapshot.technicalEvents, true))

  heading('Hallazgos registrados')
  row('Resumen', summaryText(snapshot.findings, false))
  row(
    'Alcance',
    'Hallazgos registrados por los analizadores disponibles. La ausencia de eventos registrados no excluye arritmias ni reemplaza la revisión médica.',
  )
  row('Registros del paciente', patientReportsText(snapshot.patientReports))

  heading('Conclusión')
  row('Interpretación final', snapshot.clinicalContext.conclusion)
  if (input.documentStatus === 'final') {
    row(
      'Trazabilidad',
      `${input.generatedBy?.fullName ?? 'Usuario autenticado'} · ${roleLabel(input.generatedBy?.role)} · ${formatDate(input.generatedAt)}`,
    )
  } else {
    row('Trazabilidad', 'Documento de trabajo no finalizado.')
  }

  const merged = mergeWindowPieces(input.detailWindows)
  for (const plan of input.windowPlans) {
    const window = merged.get(plan.id)
    if (!window) continue
    nextPage()
    title(`Trazado · ${annotationLabel(plan.kind)}`)
    const isMetric = plan.category === 'metric'
    row(
      'Clasificación',
      isMetric
        ? categoryLabel(plan.category)
        : `${categoryLabel(plan.category)} · severidad ${severityLabel(plan.severity)}`,
    )
    if (isMetric) row('Valor', metricWindowValue(plan, snapshot.metrics))
    row('Fecha / hora', formatDate(plan.findingStartEpochMs))
    if (!isMetric || plan.findingDurationMs > 0) {
      row('Duración total', formatDuration(plan.findingDurationMs))
    }
    if (!isMetric) row('Bloque', `${plan.blockIndex} de ${plan.blockCount}`)
    row(
      'Señal',
      window.source === 'filtered_visualization'
        ? 'Filtrada para visualización (0,05–40 Hz, notch 50 Hz); no usar para amplitud diagnóstica de QRS'
        : window.source === 'envelope'
          ? 'Envolvente de visualización'
          : 'Señal sin filtrar',
    )
    if (!isMetric) {
      row(
        'Confianza',
        plan.confidenceScore === null
          ? 'No informada'
          : `${Math.round(plan.confidenceScore * 100)} %`,
      )
    }
    row(
      'Síntomas / detalle relacionado',
      [...plan.relatedSymptoms, plan.description].filter(Boolean).join(' · '),
    )
    drawTrace(doc, window, y, snapshot.quality.timeVerified !== false)
  }

  const pages = doc.getNumberOfPages()
  for (let pageNumber = 1; pageNumber <= pages; pageNumber++) {
    doc.setPage(pageNumber)
    if (input.documentStatus === 'draft') drawDraftMark(doc)
    doc.setDrawColor(180)
    doc.line(MARGIN, PAGE_HEIGHT - 9, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 9)
    doc.setFontSize(7.5)
    doc.setTextColor(90)
    doc.text(
      `Informe Holter · ${snapshot.patient.fullName} · Página ${pageNumber} de ${pages}`,
      MARGIN,
      PAGE_HEIGHT - 5,
    )
  }
  return doc.output('arraybuffer')
}

function drawTrace(doc: jsPDF, detail: EcgReportWindow, top: number, timeVerified: boolean) {
  const left = MARGIN + 15
  const width = CONTENT_WIDTH - 15
  const height = 88
  const finite = detail.samplesMv.filter(Number.isFinite)
  const low = finite.length ? Math.min(...finite) : -1
  const high = finite.length ? Math.max(...finite) : 1
  const span = Math.max(0.2, high - low)
  const center = (high + low) / 2
  const min = center - span * 0.6
  const max = center + span * 0.6
  doc.setFillColor(255, 253, 253)
  doc.rect(left, top, width, height, 'F')
  doc.setDrawColor(...GRID)
  doc.setLineWidth(0.1)
  for (let fraction = 0; fraction <= 1.001; fraction += 0.1) {
    doc.line(left + width * fraction, top, left + width * fraction, top + height)
    doc.line(left, top + height * fraction, left + width, top + height * fraction)
  }
  const envelope = minMaxEnvelope(detail, Math.max(2, Math.round(width * 4)))
  doc.setDrawColor(...INK)
  doc.setLineWidth(0.25)
  for (const point of envelope) {
    const x =
      left +
      ((point.timestamp - detail.startEpochMs) /
        Math.max(1, detail.endEpochMs - detail.startEpochMs)) *
        width
    const y1 = top + height - ((point.min - min) / (max - min)) * height
    const y2 = top + height - ((point.max - min) / (max - min)) * height
    doc.line(x, clamp(y1, top, top + height), x, clamp(y2, top, top + height))
  }
  doc.setDrawColor(120)
  doc.rect(left, top, width, height)
  doc.setFontSize(6.5)
  doc.setTextColor(90)
  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
    const timestamp = detail.startEpochMs + (detail.endEpochMs - detail.startEpochMs) * fraction
    doc.text(formatAxisTime(timestamp), left + width * fraction, top + height + 4, {
      align: fraction === 0 ? 'left' : fraction === 1 ? 'right' : 'center',
    })
  }
  doc.text(`${max.toFixed(2)} mV`, left - 2, top + 2, { align: 'right' })
  doc.text(`${min.toFixed(2)} mV`, left - 2, top + height, { align: 'right' })
  doc.text(
    `${timeVerified ? 'Hora de Buenos Aires' : 'Hora estimada, no verificada'} · amplitud en mV · ${detail.gapIndices.length} hueco${detail.gapIndices.length === 1 ? '' : 's'}`,
    left,
    top + height + 8,
  )
  doc.setTextColor(0)
}

function minMaxEnvelope(detail: EcgReportWindow, buckets: number) {
  const duration = Math.max(1, detail.endEpochMs - detail.startEpochMs)
  const mins = new Float64Array(buckets).fill(Infinity)
  const maxs = new Float64Array(buckets).fill(-Infinity)
  for (let index = 0; index < detail.timestampsMs.length; index++) {
    const timestamp = detail.timestampsMs[index]
    const value = detail.samplesMv[index]
    if (!Number.isFinite(timestamp) || !Number.isFinite(value)) continue
    const bucket = Math.min(
      buckets - 1,
      Math.max(0, Math.floor(((timestamp - detail.startEpochMs) / duration) * buckets)),
    )
    mins[bucket] = Math.min(mins[bucket], value)
    maxs[bucket] = Math.max(maxs[bucket], value)
  }
  return Array.from({ length: buckets }, (_, bucket) => ({
    timestamp: detail.startEpochMs + (duration * (bucket + 0.5)) / buckets,
    min: mins[bucket],
    max: maxs[bucket],
  })).filter((point) => Number.isFinite(point.min) && Number.isFinite(point.max))
}

function mergeWindowPieces(windows: EcgReportWindow[]): Map<string, EcgReportWindow> {
  const grouped = new Map<string, EcgReportWindow[]>()
  for (const window of windows) grouped.set(window.id, [...(grouped.get(window.id) ?? []), window])
  return new Map(
    [...grouped.entries()].map(([id, pieces]) => {
      const ordered = pieces.sort((a, b) => a.startEpochMs - b.startEpochMs)
      const samplesMv: number[] = []
      const timestampsMs: number[] = []
      const gapIndices: number[] = []
      for (const piece of ordered) {
        const offset = samplesMv.length
        samplesMv.push(...piece.samplesMv)
        timestampsMs.push(...piece.timestampsMs)
        gapIndices.push(...piece.gapIndices.map((index) => offset + index))
        if (offset > 0) gapIndices.push(offset)
      }
      return [
        id,
        {
          id,
          startEpochMs: ordered[0].startEpochMs,
          endEpochMs: ordered.at(-1)!.endEpochMs,
          samplesMv,
          timestampsMs,
          gapIndices,
          source: ordered.some((piece) => piece.source === 'envelope')
            ? 'envelope'
            : ordered.some((piece) => piece.source === 'filtered_visualization')
              ? 'filtered_visualization'
              : 'raw',
        },
      ]
    }),
  )
}

function drawDraftMark(doc: jsPDF) {
  // Translúcida: se dibuja después del contenido y, opaca, tapaba los títulos
  // de los recuadros de la hoja resumen.
  doc.setGState(doc.GState({ opacity: 0.12 }))
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(42)
  doc.setTextColor(...INK)
  doc.text('BORRADOR', PAGE_WIDTH / 2, PAGE_HEIGHT / 2, { align: 'center', angle: 35 })
  doc.setGState(doc.GState({ opacity: 1 }))
  doc.setTextColor(0)
}

function summaryText(items: StudyClinicalReportFindingSummary[], technical: boolean): string {
  if (items.length === 0) {
    return technical
      ? 'No se registraron eventos técnicos o de calidad.'
      : 'No se registraron hallazgos con los analizadores disponibles.'
  }
  return items
    .map(
      (item) =>
        `${annotationLabel(item.kind)}: ${item.count}; severidad ${item.severities.join('/')}; duración total ${formatDuration(item.totalDurationMs)}; episodio más largo ${formatDuration(item.longestDurationMs)}${technical ? '' : `; con síntomas relacionados ${item.symptomaticCount}`}`,
    )
    .join(' · ')
}

function patientReportsText(reports: ClinicalReportInput['snapshot']['patientReports']): string {
  if (reports.length === 0) return 'Sin registros sintomáticos informados por el paciente.'
  return reports
    .map((report) => {
      const symptoms = [...report.symptoms, report.symptomsOther].filter(Boolean).join(', ') || '—'
      return `${formatDate(report.occurredAt)}: ${symptoms}; actividad ${report.activityOther || report.activity || '—'}${report.notes ? `; ${report.notes}` : ''}`
    })
    .join(' · ')
}

export function automaticAnnotations(annotations: ECGAnnotation[]): ECGAnnotation[] {
  return annotations.filter(
    (item) => item.category === 'clinical' && item.linkedAnnotationId === null,
  )
}

function roleLabel(role: string | undefined): string {
  return role === 'admin' ? 'Administrador' : role === 'medico' ? 'Médico' : role || '—'
}

function categoryLabel(category: StudyClinicalReportWindowPlan['category']): string {
  if (category === 'metric') return 'Evidencia de métrica del Holter'
  return category === 'clinical' ? 'Hallazgo clínico' : 'Registro sintomático'
}

function metricWindowValue(
  plan: StudyClinicalReportWindowPlan,
  metrics: HolterMetrics | null | undefined,
): string {
  if (plan.kind === 'pause_longest') {
    return `R-R de ${formatMetricValue(plan.findingDurationMs / 1000, 2, 's')}`
  }
  const evidence = plan.kind === 'hr_min' ? metrics?.heartRate?.min : metrics?.heartRate?.max
  const window = metrics?.heartRate?.windowBeats ?? 8
  return `${formatMetricValue(evidence?.value, 0, 'lpm')} (promedio de ${window} latidos)`
}

function severityLabel(severity: string): string {
  return { low: 'baja', medium: 'media', high: 'alta', critical: 'crítica' }[severity] ?? severity
}
