import { jsPDF } from 'jspdf'

import type { EcgReportWindow } from './api/ecgApi'
import type { ECGAnnotation, ECGSignal } from './types'
import type { ClinicalReportInput } from './clinicalReportTypes'

const PAGE_WIDTH = 210
const PAGE_HEIGHT = 297
const MARGIN = 12
const CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2
const INK = [18, 46, 92] as const
const ECG_GRID_MINOR = [255, 231, 229] as const
const ECG_GRID_MAJOR = [235, 169, 165] as const

export function buildClinicalReport(input: ClinicalReportInput): ArrayBuffer {
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4', compress: true })
  const generatedAt = formatDate(input.generatedAt)
  let y = 26

  const runningHeader = () => {
    doc.setFillColor(...INK)
    doc.roundedRect(MARGIN, 8, CONTENT_WIDTH, 11, 1.5, 1.5, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(10)
    doc.setTextColor(255)
    doc.text('HOLTER ECG · INFORME CLÍNICO', MARGIN + 4, 15)
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(8)
    doc.text(input.patient.fullName, PAGE_WIDTH - MARGIN - 4, 15, { align: 'right' })
    doc.setTextColor(0)
    y = 26
  }

  const page = () => {
    doc.addPage()
    runningHeader()
  }
  const title = (value: string) => {
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(15)
    doc.text(value, MARGIN, y)
    y += 8
    doc.setFont('helvetica', 'normal')
  }
  const row = (label: string, value: string) => {
    const labelWidth = 43
    const lines = doc.splitTextToSize(value || '—', CONTENT_WIDTH - labelWidth - 3)
    const rowHeight = Math.max(6, lines.length * 4.3 + 2)
    if (y + rowHeight > PAGE_HEIGHT - MARGIN) page()
    doc.setFillColor(247, 249, 252)
    doc.roundedRect(MARGIN, y - 3.7, CONTENT_WIDTH, rowHeight, 0.8, 0.8, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(9)
    doc.setTextColor(...INK)
    doc.text(label, MARGIN + 2, y)
    doc.setFont('helvetica', 'normal')
    doc.setTextColor(0)
    doc.text(lines, MARGIN + labelWidth, y)
    y += rowHeight + 1
  }
  const heading = (value: string) => {
    if (y + 12 > PAGE_HEIGHT - MARGIN) page()
    doc.setFillColor(...INK)
    doc.roundedRect(MARGIN, y - 4.3, CONTENT_WIDTH, 7, 1, 1, 'F')
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(11)
    doc.setTextColor(255)
    doc.text(value, MARGIN + 3, y)
    doc.setTextColor(0)
    y += 8
    doc.setFont('helvetica', 'normal')
  }

  runningHeader()
  title('Informe de estudio ECG')
  row('Generado', generatedAt)
  row('Paciente', input.patient.fullName)
  row('DNI', input.patient.dni)
  row(
    'Nacimiento / edad',
    `${formatCalendarDate(input.patient.birthDate)} / ${age(input.patient.birthDate)} años`,
  )
  row('Sexo', input.patient.sex)
  row('Estudio', input.study.id)
  row('Estado', studyStatus(input.study.status))
  row('Inicio', formatDate(input.study.startedAt))
  row(
    'Fin',
    input.study.endedAt ? formatDate(input.study.endedAt) : `En curso - corte ${generatedAt}`,
  )
  row('Duración del estudio', formatDuration(input.study.durationMs))
  row('Holter', input.study.deviceSerial)

  heading('Adquisición y calidad técnica')
  const recordedMs =
    ((input.signal.metadata?.sampleCount ?? input.signal.samples.length) * 1000) /
    input.signal.sampleRate
  const wallMs = input.signal.durationMs
  const interruptionMs = Math.max(0, wallMs - recordedMs)
  const coverage = wallMs > 0 ? Math.min(100, (recordedMs / wallMs) * 100) : 0
  row('Frecuencia de muestreo', `${input.signal.sampleRate} Hz`)
  row(
    'Muestras originales',
    String(input.signal.metadata?.sampleCount ?? input.signal.samples.length),
  )
  row('Tiempo grabado', formatDuration(recordedMs))
  row('Lapso de pared', formatDuration(wallMs))
  row('Interrupciones estimadas', formatDuration(interruptionMs))
  row('Cobertura', `${coverage.toFixed(1)} %`)
  row(
    'Segmentos / cortes',
    `${input.signal.timeline.length || 1} / ${Math.max(0, input.signal.timeline.length - 1)}`,
  )
  row(
    'Último dato recibido',
    input.study.lastDataReceivedAt ? formatDate(input.study.lastDataReceivedAt) : '—',
  )
  row('Sincronización', synchronizationText(input.signal))
  row(
    'Formato',
    `${input.signal.metadata?.encoding ?? 'float32-le'}${input.signal.metadata?.isSimulated ? ' - señal simulada' : ''}`,
  )
  row(
    'Overview',
    input.signal.metadata?.overviewSamplesPerBucket
      ? `envolvente cada ${input.signal.metadata.overviewSamplesPerBucket} muestras`
      : 'señal cruda',
  )

  heading('Hallazgos automáticos')
  const automatedFindings = automaticAnnotations(input.signal.annotations)
  row('Resumen', findingsSummary(automatedFindings))
  row('Aviso clínico', 'Los hallazgos son automáticos y requieren validación médica.')
  for (const annotation of automatedFindings) {
    row(
      `${annotationLabel(annotation)} (${annotation.severity})`,
      `${formatDate(annotation.startMs)}${annotation.endMs > annotation.startMs ? ` - ${formatDuration(annotation.endMs - annotation.startMs)}` : ''}${annotation.confidenceScore !== null ? ` - confianza ${(annotation.confidenceScore * 100).toFixed(0)} %` : ''}${annotation.description ? ` - ${annotation.description}` : ''}`,
    )
  }

  heading('Reportes del paciente')
  if (input.reports.length === 0) row('Reportes', 'Sin registros del paciente.')
  for (const report of input.reports) {
    const symptoms =
      [...report.symptomLabels, report.symptomsOther].filter(Boolean).join(' · ') || '—'
    row(
      formatDate(report.occurredAt),
      `Síntomas: ${symptoms}. Actividad: ${report.activityOther || report.activityLabel || '—'}. Nota: ${report.notes || '—'}. ${report.alertKind ? `Relacionado con: ${report.alertKind}.` : ''}${report.visibleInChart ? '' : ' Aún sin señal debajo.'}`,
    )
  }

  const sectionMs = input.sectionMinutes * 60_000
  for (
    let sectionStart = input.signal.startTimestamp;
    sectionStart < input.signal.startTimestamp + input.signal.durationMs;
    sectionStart += sectionMs
  ) {
    if (y + 58 > PAGE_HEIGHT - MARGIN) page()
    drawOverview(
      doc,
      input.signal,
      sectionStart,
      Math.min(sectionStart + sectionMs, input.signal.startTimestamp + input.signal.durationMs),
      input.sectionMinutes,
      y,
    )
    y += 64
  }

  for (const detail of input.detailWindows) {
    page()
    drawDetail(doc, detail, input.paperSpeed, input.amplitude, y)
  }

  const pages = doc.getNumberOfPages()
  for (let pageNumber = 1; pageNumber <= pages; pageNumber++) {
    doc.setPage(pageNumber)
    doc.setDrawColor(180)
    doc.line(MARGIN, PAGE_HEIGHT - 9, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 9)
    doc.setFontSize(8)
    doc.setTextColor(90)
    doc.text(
      `Informe ECG · ${input.patient.fullName} · Página ${pageNumber} de ${pages}`,
      MARGIN,
      PAGE_HEIGHT - 5,
    )
    doc.setTextColor(0)
  }
  return doc.output('arraybuffer')
}

function drawOverview(
  doc: jsPDF,
  signal: ECGSignal,
  startMs: number,
  endMs: number,
  minutes: number,
  top: number,
) {
  const axisWidth = 15
  const plotLeft = MARGIN + axisWidth
  const plotWidth = CONTENT_WIDTH - axisWidth
  const plotTop = top + 6
  const plotHeight = 42
  doc.setFontSize(8)
  doc.setFont('helvetica', 'bold')
  doc.setTextColor(...INK)
  doc.text('ECG — overview temporal comprimido', MARGIN, top)
  doc.setFont('helvetica', 'normal')
  doc.setTextColor(0)
  doc.setFontSize(8)
  doc.text(
    `${formatDate(startMs)} - ${formatDate(endMs)} · ${minutes} min · no apto para medir intervalos`,
    PAGE_WIDTH - MARGIN,
    top,
    { align: 'right' },
  )
  let min = Infinity
  let max = -Infinity
  for (let i = 0; i < signal.samples.length; i++) {
    if (signal.timestampsMs[i] < startMs || signal.timestampsMs[i] > endMs) continue
    const value = signal.samples[i]
    if (Number.isFinite(value)) {
      min = Math.min(min, value)
      max = Math.max(max, value)
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return
  const span = Math.max(max - min, 0.2)
  const center = (max + min) / 2
  min = center - span / 2
  max = center + span / 2
  drawOverviewGrid(doc, plotLeft, plotTop, plotWidth, plotHeight, startMs, endMs, min, max)
  let previous: [number, number] | null = null
  const gaps = new Set(signal.gapIndices)
  doc.setDrawColor(...INK)
  doc.setLineWidth(0.25)
  for (let i = 0; i < signal.samples.length; i++) {
    const timestamp = signal.timestampsMs[i]
    if (timestamp < startMs || timestamp > endMs || gaps.has(i)) {
      previous = null
      continue
    }
    const value = signal.samples[i]
    if (!Number.isFinite(value)) {
      previous = null
      continue
    }
    const point: [number, number] = [
      plotLeft + ((timestamp - startMs) / (endMs - startMs)) * plotWidth,
      plotTop + plotHeight - ((value - min) / span) * plotHeight,
    ]
    if (previous) doc.line(previous[0], previous[1], point[0], point[1])
    previous = point
  }
  doc.setLineWidth(0.2)
}

function drawDetail(
  doc: jsPDF,
  detail: EcgReportWindow,
  paperSpeed: number,
  amplitude: number,
  contentTop: number,
) {
  const axisWidth = 15
  const plotLeft = MARGIN + axisWidth
  const plotWidth = CONTENT_WIDTH - axisWidth
  const plotHeight = 40
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')
  doc.setTextColor(...INK)
  doc.text(
    `Tira detallada · ${formatDate(detail.startEpochMs)} · fuente: ${detail.source}`,
    MARGIN,
    contentTop,
  )
  doc.setFont('helvetica', 'normal')
  doc.setTextColor(0)
  const secondsPerLine = Math.max(1, Math.floor(plotWidth / paperSpeed))
  const lineMs = secondsPerLine * 1000
  const center = median(detail.samplesMv)
  for (
    let lineStart = detail.startEpochMs, row = 0;
    lineStart < detail.endEpochMs;
    lineStart += lineMs, row++
  ) {
    const top = contentTop + 12 + row * 54
    let previous: [number, number] | null = null
    const lineEnd = Math.min(lineStart + lineMs, detail.endEpochMs)
    drawClinicalGrid(
      doc,
      plotLeft,
      top,
      plotWidth,
      plotHeight,
      lineStart,
      lineEnd,
      center,
      paperSpeed,
      amplitude,
    )
    drawCalibrationPulse(doc, MARGIN + 1, top + plotHeight / 2, amplitude)
    doc.setDrawColor(...INK)
    doc.setLineWidth(0.3)
    const gaps = new Set(detail.gapIndices)
    for (let i = 0; i < detail.samplesMv.length; i++) {
      const timestamp = detail.timestampsMs[i]
      if (timestamp < lineStart || timestamp > lineEnd || gaps.has(i)) {
        previous = null
        continue
      }
      const point: [number, number] = [
        plotLeft + ((timestamp - lineStart) / 1000) * paperSpeed,
        top + plotHeight / 2 - (detail.samplesMv[i] - center) * amplitude,
      ]
      if (previous) doc.line(previous[0], previous[1], point[0], point[1])
      previous = point
    }
    doc.setFontSize(8)
    doc.text(`${formatDate(lineStart)} · ${paperSpeed} mm/s · ${amplitude} mm/mV`, MARGIN, top - 3)
    doc.setLineWidth(0.2)
  }
}

function drawCalibrationPulse(doc: jsPDF, left: number, baseline: number, amplitude: number) {
  const width = 5
  const height = amplitude
  doc.setDrawColor(...INK)
  doc.setLineWidth(0.3)
  doc.line(left, baseline, left + 1, baseline)
  doc.line(left + 1, baseline, left + 1, baseline - height)
  doc.line(left + 1, baseline - height, left + width - 1, baseline - height)
  doc.line(left + width - 1, baseline - height, left + width - 1, baseline)
  doc.line(left + width - 1, baseline, left + width, baseline)
  doc.setFontSize(6)
  doc.text('1 mV', left, baseline + 4)
}

function drawOverviewGrid(
  doc: jsPDF,
  left: number,
  top: number,
  width: number,
  height: number,
  startMs: number,
  endMs: number,
  min: number,
  max: number,
) {
  doc.setFillColor(255, 253, 253)
  doc.rect(left, top, width, height, 'F')
  doc.setDrawColor(...ECG_GRID_MINOR)
  for (let fraction = 0; fraction <= 1; fraction += 0.125) {
    const x = left + width * fraction
    doc.line(x, top, x, top + height)
  }
  for (let fraction = 0; fraction <= 1; fraction += 0.25) {
    const y = top + height * fraction
    doc.line(left, y, left + width, y)
  }
  doc.setDrawColor(...ECG_GRID_MAJOR)
  doc.rect(left, top, width, height)
  doc.setFontSize(6.5)
  doc.setTextColor(100)
  for (const fraction of [0, 0.5, 1]) {
    const timestamp = startMs + (endMs - startMs) * fraction
    doc.text(formatAxisTime(timestamp), left + width * fraction, top + height + 4, {
      align: fraction === 0 ? 'left' : fraction === 1 ? 'right' : 'center',
    })
    const value = max - (max - min) * fraction
    doc.text(`${value.toFixed(2)} mV`, left - 2, top + height * fraction + 1, { align: 'right' })
  }
  doc.text('Tiempo', left + width / 2, top + height + 7, { align: 'center' })
  doc.text('mV', left - 8, top - 2, { align: 'right' })
  doc.setTextColor(0)
}

function drawClinicalGrid(
  doc: jsPDF,
  left: number,
  top: number,
  width: number,
  height: number,
  lineStart: number,
  lineEnd: number,
  center: number,
  paperSpeed: number,
  amplitude: number,
) {
  doc.setFillColor(255, 253, 253)
  doc.rect(left, top, width, height, 'F')
  doc.setLineWidth(0.08)
  doc.setDrawColor(...ECG_GRID_MINOR)
  for (let x = left; x <= left + width + 0.01; x += 1) doc.line(x, top, x, top + height)
  for (let y = top; y <= top + height + 0.01; y += 1) doc.line(left, y, left + width, y)
  doc.setLineWidth(0.15)
  doc.setDrawColor(...ECG_GRID_MAJOR)
  for (let x = left; x <= left + width + 0.01; x += 5) doc.line(x, top, x, top + height)
  for (let y = top; y <= top + height + 0.01; y += 5) doc.line(left, y, left + width, y)
  doc.rect(left, top, width, height)
  doc.setFontSize(6.5)
  doc.setTextColor(100)
  const secondsPerMajorGrid = 5 / paperSpeed
  for (let seconds = 0; seconds <= (lineEnd - lineStart) / 1000 + 0.001; seconds += 1) {
    doc.text(`+${seconds.toFixed(0)} s`, left + seconds * paperSpeed, top + height + 4, {
      align: seconds === 0 ? 'left' : 'center',
    })
  }
  for (let y = top; y <= top + height + 0.01; y += 10) {
    const value = center + (top + height / 2 - y) / amplitude
    doc.text(`${value.toFixed(1)}`, left - 2, y + 1, { align: 'right' })
  }
  doc.text(
    `Tiempo (${secondsPerMajorGrid.toFixed(2)} s/cuadro grande)`,
    left + width / 2,
    top + height + 7,
    {
      align: 'center',
    },
  )
  doc.text('mV', left - 8, top - 2, { align: 'right' })
  doc.setTextColor(0)
}

function median(values: number[]): number {
  if (values.length === 0) return 0
  const ordered = [...values].filter(Number.isFinite).sort((a, b) => a - b)
  return ordered[Math.floor(ordered.length / 2)] ?? 0
}

function findingsSummary(annotations: ECGAnnotation[]): string {
  if (annotations.length === 0) return 'Sin hallazgos automáticos.'
  const counts = new Map<string, number>()
  for (const item of annotations) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1)
  return [...counts.entries()].map(([kind, count]) => `${kind}: ${count}`).join(' · ')
}

export function automaticAnnotations(annotations: ECGAnnotation[]): ECGAnnotation[] {
  return annotations.filter((annotation) => annotation.category !== 'patient_marker')
}

function synchronizationText(signal: ECGSignal): string {
  if (signal.timeline.length === 0) return 'No disponible para estudio legacy.'
  const sources = [...new Set(signal.timeline.map((item) => item.anchorSource))].join(', ')
  const uncertainty = Math.max(...signal.timeline.map((item) => item.anchorUncertaintyMs ?? 0))
  return `${sources}; incertidumbre máxima ${uncertainty} ms`
}

function annotationLabel(annotation: ECGAnnotation): string {
  return annotation.kind.replaceAll('_', ' ')
}

function studyStatus(status: ClinicalReportInput['study']['status']): string {
  return {
    in_progress: 'En curso',
    completed: 'Completado',
    cancelled: 'Cancelado',
    scheduled: 'Programado',
  }[status]
}

function formatDate(value: string | number): string {
  return new Intl.DateTimeFormat('es-AR', { dateStyle: 'short', timeStyle: 'medium' }).format(
    new Date(value),
  )
}

function formatAxisTime(value: number): string {
  return new Intl.DateTimeFormat('es-AR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function formatCalendarDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return value
  return new Intl.DateTimeFormat('es-AR', { dateStyle: 'short' }).format(
    new Date(year, month - 1, day),
  )
}

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = seconds % 60
  return `${hours} h ${minutes} min ${remaining} s`
}

function age(birthDate: string): number {
  const birth = new Date(birthDate)
  const now = new Date()
  let value = now.getFullYear() - birth.getFullYear()
  if (
    now.getMonth() < birth.getMonth() ||
    (now.getMonth() === birth.getMonth() && now.getDate() < birth.getDate())
  )
    value--
  return Math.max(0, value)
}
