import type { jsPDF } from 'jspdf'

import type { HolterMetrics } from '@/features/studies/types'
import type {
  HolterEctopyOut,
  HolterStChannelOut,
  HolterStKindOut,
  MetricEvidenceOut,
} from '@/generated/openapi'

import type { ClinicalReportInput } from './clinicalReportTypes'
import {
  age,
  BOX_FILL,
  CONTENT_WIDTH,
  formatAxisTime,
  formatCalendarDate,
  formatDate,
  formatDuration,
  formatHour,
  formatMetricValue,
  INK,
  MARGIN,
  NOT_AVAILABLE,
  PAGE_HEIGHT,
  sexLabel,
} from './clinicalReportFormat'

/*
 * Páginas 1 y 2 del informe, con la estructura de un informe Holter comercial:
 * una hoja resumen con recuadros (FC, pausas, S, V, VFC, ST), la conclusión y
 * la firma, y una hoja de tendencias. El detalle (contexto, calidad, hallazgos
 * y tiras) va después, en `clinicalReport.ts`.
 */

const GAP = 3
const COLUMN = (CONTENT_WIDTH - GAP) / 2
const LINE = 4.3
const BOX_TITLE = 6.5

type Row = [label: string, value: string, extra?: string]

/** Por qué una métrica no está, en palabras del médico. */
export function metricsStatusText(metrics: HolterMetrics | null | undefined): string | null {
  if (!metrics) return 'Métricas no incluidas en este informe.'
  switch (metrics.status) {
    case 'ok':
      return null
    case 'pending':
      return 'Análisis de latidos pendiente.'
    case 'insufficient_data':
      return 'Sin datos analizables.'
    default:
      return 'El estudio no tiene señal analizable.'
  }
}

function evidenceTime(evidence: MetricEvidenceOut | null | undefined): string | undefined {
  return evidence ? `Fecha: ${formatDate(evidence.epochMs)}` : undefined
}

function box(doc: jsPDF, x: number, y: number, width: number, height: number, title: string) {
  doc.setDrawColor(...INK)
  doc.setLineWidth(0.3)
  doc.rect(x, y, width, height)
  doc.setFillColor(...BOX_FILL)
  doc.rect(x, y, width, BOX_TITLE, 'F')
  doc.line(x, y + BOX_TITLE, x + width, y + BOX_TITLE)
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(8.5)
  doc.setTextColor(...INK)
  doc.text(title, x + 2.5, y + 4.6)
  doc.setTextColor(0)
}

function rows(doc: jsPDF, x: number, y: number, width: number, items: Row[]) {
  let cursor = y
  for (const [label, value, extra] of items) {
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(7.8)
    doc.text(label, x + 3, cursor)
    doc.setFont('helvetica', 'bold')
    doc.text(value, x + width * 0.5, cursor, { align: 'right' })
    if (extra) {
      doc.setFont('helvetica', 'normal')
      doc.setFontSize(7)
      doc.setTextColor(70)
      doc.text(extra, x + width - 2.5, cursor, { align: 'right' })
      doc.setTextColor(0)
    }
    cursor += LINE
  }
  return cursor
}

function note(doc: jsPDF, x: number, y: number, width: number, text: string) {
  doc.setFont('helvetica', 'italic')
  doc.setFontSize(6.6)
  doc.setTextColor(90)
  const lines = doc.splitTextToSize(text, width - 5)
  doc.text(lines, x + 3, y)
  doc.setTextColor(0)
  return y + lines.length * 3
}

function ectopyRows(
  ectopy: HolterEctopyOut | null | undefined,
  letter: 'S' | 'V',
  unit: string,
): Row[] {
  const count = (episodes?: number, beats?: number): [string, string | undefined] =>
    ectopy && episodes !== undefined
      ? [formatMetricValue(episodes), `Total: ${formatMetricValue(beats)} ${unit}`]
      : [NOT_AVAILABLE, undefined]
  const pairs = count(ectopy?.pairs.episodes, ectopy?.pairs.beats)
  const bigeminy = count(ectopy?.bigeminy.episodes, ectopy?.bigeminy.beats)
  const trigeminy = count(ectopy?.trigeminy.episodes, ectopy?.trigeminy.beats)
  const runs = count(ectopy?.runs.episodes, ectopy?.runs.beats)
  return [
    [
      `${letter} total:`,
      formatMetricValue(ectopy?.total),
      ectopy ? `Aisladas: ${formatMetricValue(ectopy.single)} ${unit}` : undefined,
    ],
    [`Pares ${letter}:`, ...pairs],
    ['Bigeminismo:', ...bigeminy],
    ['Trigeminismo:', ...trigeminy],
    [`Corridas ${letter}:`, ...runs],
    [`${letter} por mil:`, formatMetricValue(ectopy?.perThousand, 1)],
    [
      `Máx. ${letter} en 1 min:`,
      formatMetricValue(ectopy?.maxPerMinute?.value),
      evidenceTime(ectopy?.maxPerMinute),
    ],
  ]
}

function stCells(kind: HolterStKindOut | undefined): string[] {
  if (!kind) return [NOT_AVAILABLE, NOT_AVAILABLE, NOT_AVAILABLE]
  return [
    formatMetricValue(kind.durationSeconds),
    kind.maxDeviation ? formatMetricValue(kind.maxDeviation.value, 2) : '0',
    kind.maxSlopeMvPerMin === null ? '—' : formatMetricValue(kind.maxSlopeMvPerMin, 2),
  ]
}

function stTable(doc: jsPDF, x: number, y: number, width: number, channels: HolterStChannelOut[]) {
  const labelWidth = 22
  const cell = (width - labelWidth - 3) / 6
  const columnX = (index: number) => x + labelWidth + cell * index + cell / 2
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(7.6)
  doc.text('Elevación', columnX(1), y, { align: 'center' })
  doc.text('Depresión', columnX(4), y, { align: 'center' })
  doc.setDrawColor(150)
  doc.setLineWidth(0.15)
  doc.line(x + labelWidth, y + 1.2, x + labelWidth + cell * 3 - 1, y + 1.2)
  doc.line(x + labelWidth + cell * 3 + 1, y + 1.2, x + labelWidth + cell * 6, y + 1.2)
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(6.8)
  const units = ['(seg.)', '(mV)', '(mV/min)']
  for (let index = 0; index < 6; index++) {
    doc.text(units[index % 3], columnX(index), y + 4.6, { align: 'center' })
  }
  let cursor = y + 10
  for (const channel of channels) {
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(7.6)
    doc.text(doc.splitTextToSize(channel.label, labelWidth - 3), x + 3, cursor)
    doc.setFont('helvetica', 'normal')
    const cells = [...stCells(channel.elevation), ...stCells(channel.depression)]
    cells.forEach((value, index) => doc.text(value, columnX(index), cursor, { align: 'center' }))
    cursor += 9
  }
  return cursor
}

/**
 * Página 1. `startY` es donde termina la cabecera de la página. Dibuja todo con
 * alturas fijas: la conclusión se recorta si no entra, porque el resumen de un
 * Holter es una sola hoja y el texto completo se repite en el detalle.
 */
export function drawSummaryPage(doc: jsPDF, input: ClinicalReportInput, startY: number) {
  const { snapshot } = input
  const metrics = snapshot.metrics
  const status = metricsStatusText(metrics)
  const ok = metrics?.status === 'ok'
  let y = startY

  doc.setFont('helvetica', 'bold')
  doc.setFontSize(14)
  doc.setTextColor(...INK)
  doc.text('CARDIOLOGÍA · Informe de Holter ECG', MARGIN + CONTENT_WIDTH / 2, y, {
    align: 'center',
  })
  doc.setTextColor(0)
  y += 4

  // --- Datos del paciente y del estudio ---------------------------------- //
  const { patient, study, responsibleDoctor, clinicalContext } = snapshot
  const analyzedMs = metrics?.analysis?.analyzedMs
  const header: Array<[string, string]> = [
    ['Paciente', patient.fullName],
    ['Sexo', sexLabel(patient.sex)],
    ['Edad', patient.birthDate ? `${age(patient.birthDate)} años` : '—'],
    ['DNI', patient.dni || '—'],
    ['Nacimiento', patient.birthDate ? formatCalendarDate(patient.birthDate) : '—'],
    ['N.º de HC', patient.medicalRecordNumber || '—'],
    [
      'Inicio',
      study.startedAtVerified === false ? 'Hora no verificada' : formatDate(study.startedAt),
    ],
    ['Fin', study.endedAt ? formatDate(study.endedAt) : 'En curso'],
    ['Dispositivo', study.deviceSerial],
    ['Tiempo total', formatDuration(study.durationMs)],
    ['Tiempo analizado', analyzedMs === undefined ? NOT_AVAILABLE : formatDuration(analyzedMs)],
    ['Canales', ok ? `${metrics.st.length} (${metrics.st.map((c) => c.label).join(', ')})` : '—'],
    ['Médico', responsibleDoctor.fullName || '—'],
    ['Derivante', clinicalContext.referringProfessional || '—'],
    ['Matrícula', responsibleDoctor.licenseNumber || '—'],
  ]
  const cellWidth = CONTENT_WIDTH / 3
  const headerHeight = Math.ceil(header.length / 3) * 5 + 3
  doc.setDrawColor(...INK)
  doc.setLineWidth(0.3)
  doc.rect(MARGIN, y, CONTENT_WIDTH, headerHeight)
  header.forEach(([label, value], index) => {
    const cellX = MARGIN + (index % 3) * cellWidth + 2.5
    const cellY = y + 5 + Math.floor(index / 3) * 5
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(7.6)
    doc.setTextColor(70)
    doc.text(`${label}:`, cellX, cellY)
    const labelWidth = doc.getTextWidth(`${label}: `)
    doc.setFont('helvetica', 'bold')
    doc.setTextColor(0)
    const fitted = doc.splitTextToSize(value, cellWidth - labelWidth - 4)[0] ?? ''
    doc.text(fitted, cellX + labelWidth, cellY)
  })
  y += headerHeight + GAP

  const left = MARGIN
  const right = MARGIN + COLUMN + GAP

  // --- FC | Pausas --------------------------------------------------------- //
  const heart = metrics?.heartRate
  const rowA = BOX_TITLE + 6 * LINE + 6
  box(doc, left, y, COLUMN, rowA, 'Frecuencia cardíaca (FC)')
  box(doc, right, y, COLUMN, rowA, 'Tiempo de pausa')
  rows(doc, left, y + BOX_TITLE + 5, COLUMN, [
    ['FC promedio:', formatMetricValue(heart?.averageBpm, 0, 'lpm')],
    ['FC mínima:', formatMetricValue(heart?.min?.value, 0, 'lpm'), evidenceTime(heart?.min)],
    ['FC máxima:', formatMetricValue(heart?.max?.value, 0, 'lpm'), evidenceTime(heart?.max)],
    ['Latidos totales:', formatMetricValue(heart?.totalBeats)],
    ['Latidos anormales:', formatMetricValue(heart?.abnormalBeats)],
    ['Anormal por mil:', formatMetricValue(heart?.abnormalPerThousand, 1)],
  ])
  const pauses = metrics?.pauses
  const pauseRows: Row[] = [
    [`Pausas R-R > ${pauses?.thresholdMs ?? 2000} ms:`, formatMetricValue(pauses?.count)],
    [
      'Pausa más larga:',
      pauses?.longest ? formatMetricValue((pauses.longest.durationMs ?? 0) / 1000, 2, 's') : '—',
      evidenceTime(pauses?.longest),
    ],
    ...(pauses?.items ?? [])
      .slice(1, 4)
      .map(
        (item): Row => [
          'Otra pausa:',
          formatMetricValue((item.durationMs ?? 0) / 1000, 2, 's'),
          evidenceTime(item),
        ],
      ),
  ]
  rows(doc, right, y + BOX_TITLE + 5, COLUMN, pauseRows)
  if (status) note(doc, left, y + rowA - 2.5, COLUMN, status)
  y += rowA + GAP

  // --- S | V --------------------------------------------------------------- //
  const rowB = BOX_TITLE + 7 * LINE + 9
  box(doc, left, y, COLUMN, rowB, 'Supraventriculares (S)')
  box(doc, right, y, COLUMN, rowB, 'Ventriculares (V)')
  rows(doc, left, y + BOX_TITLE + 5, COLUMN, ectopyRows(metrics?.supraventricular, 'S', 'SVE'))
  rows(doc, right, y + BOX_TITLE + 5, COLUMN, ectopyRows(metrics?.ventricular, 'V', 'VE'))
  if (metrics?.ectopyUnavailableReason) {
    const text =
      'N/D: requiere clasificación de latidos (normal / supraventricular / ventricular), que todavía no está disponible.'
    note(doc, left, y + rowB - 4.5, COLUMN, text)
    note(doc, right, y + rowB - 4.5, COLUMN, text)
  }
  y += rowB + GAP

  // --- VFC | ST ------------------------------------------------------------ //
  const time = metrics?.hrvTime
  const frequency = metrics?.hrvFrequency
  const rowC = BOX_TITLE + 12 * LINE + 5
  box(doc, left, y, COLUMN, rowC, 'Variabilidad de la FC (VFC)')
  box(doc, right, y, COLUMN, rowC, 'Análisis del segmento ST')
  let cursor = y + BOX_TITLE + 4.5
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(7.6)
  doc.text('Dominio del tiempo', left + 3, cursor)
  cursor = rows(doc, left + 2, cursor + LINE, COLUMN - 2, [
    ['SDNN (ms):', formatMetricValue(time?.sdnnMs, 1)],
    ['SDANN (ms):', formatMetricValue(time?.sdannMs, 1)],
    ['rMSSD (ms):', formatMetricValue(time?.rmssdMs, 1)],
    ['pNN50 (%):', formatMetricValue(time?.pnn50Percent, 1)],
    ['CV:', formatMetricValue(time?.cv, 2)],
  ])
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(7.6)
  doc.text('Dominio de la frecuencia (ms²)', left + 3, cursor)
  rows(doc, left + 2, cursor + LINE, COLUMN - 2, [
    ['Energía:', formatMetricValue(frequency?.totalPowerMs2, 1)],
    ['ULF:', formatMetricValue(frequency?.ulfMs2, 1)],
    ['VLF:', formatMetricValue(frequency?.vlfMs2, 1)],
    ['LF:', formatMetricValue(frequency?.lfMs2, 1)],
    ['HF:', formatMetricValue(frequency?.hfMs2, 1)],
  ])
  if (ok && metrics.st.length) {
    stTable(doc, right, y + BOX_TITLE + 5, COLUMN, metrics.st)
    const median = metrics.st[0].medianLevelMv
    note(
      doc,
      right,
      y + rowC - 12,
      COLUMN,
      `Episodio: desvío de 0,1 mV o más en minutos con señal suficiente; duración estimada por minutos completos. ST medido a R + 100 ms respecto del segmento PR, sobre señal 0,05–40 Hz. Nivel ST mediano: ${formatMetricValue(median, 2, 'mV')}.`,
    )
  } else {
    note(doc, right, y + BOX_TITLE + 5, COLUMN, status ?? 'Sin datos de ST.')
  }
  y += rowC + GAP

  // --- Conclusión y firma -------------------------------------------------- //
  const bottom = PAGE_HEIGHT - 14
  const conclusionHeight = bottom - y
  box(doc, MARGIN, y, CONTENT_WIDTH, conclusionHeight, 'Conclusión')
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(8.6)
  const available = conclusionHeight - BOX_TITLE - 26
  const lines: string[] = doc.splitTextToSize(
    clinicalContext.conclusion || 'Sin conclusión cargada.',
    CONTENT_WIDTH - 6,
  )
  const maxLines = Math.max(1, Math.floor(available / 4))
  const shown =
    lines.length > maxLines
      ? [...lines.slice(0, maxLines - 1), `${lines[maxLines - 1]} … (continúa en el detalle)`]
      : lines
  doc.text(shown, MARGIN + 3, y + BOX_TITLE + 5)

  const signatureX = MARGIN + CONTENT_WIDTH - 70
  const signatureY = y + conclusionHeight - 12
  doc.setDrawColor(60)
  doc.setLineWidth(0.25)
  doc.line(signatureX, signatureY, signatureX + 64, signatureY)
  doc.setFontSize(7.6)
  doc.text('Firma del médico', signatureX + 32, signatureY + 3.6, { align: 'center' })
  doc.setFont('helvetica', 'bold')
  doc.text(
    [responsibleDoctor.fullName, responsibleDoctor.licenseNumber].filter(Boolean).join(' · ') ||
      '—',
    signatureX + 32,
    signatureY + 7.4,
    { align: 'center' },
  )
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(6.8)
  doc.setTextColor(90)
  doc.text(
    input.documentStatus === 'final'
      ? `Emitido por ${input.generatedBy?.fullName ?? 'usuario autenticado'} · ${formatDate(input.generatedAt)}`
      : 'Borrador: documento de trabajo no finalizado.',
    MARGIN + 3,
    signatureY + 7.4,
  )
  doc.setTextColor(0)
}

// --------------------------------------------------------------------------- //
// Página 2 — tendencias
// --------------------------------------------------------------------------- //

interface Frame {
  left: number
  top: number
  width: number
  height: number
  xMin: number
  xMax: number
  yMin: number
  yMax: number
}

const px = (frame: Frame, value: number) =>
  frame.left + ((value - frame.xMin) / Math.max(1e-9, frame.xMax - frame.xMin)) * frame.width
const py = (frame: Frame, value: number) =>
  frame.top +
  frame.height -
  ((value - frame.yMin) / Math.max(1e-9, frame.yMax - frame.yMin)) * frame.height

function axes(
  doc: jsPDF,
  frame: Frame,
  title: string,
  xTicks: Array<[number, string]>,
  yTicks: Array<[number, string]>,
) {
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(8.5)
  doc.setTextColor(...INK)
  doc.text(title, frame.left - 10, frame.top - 3)
  doc.setTextColor(0)
  doc.setDrawColor(225)
  doc.setLineWidth(0.1)
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(6.4)
  doc.setTextColor(90)
  for (const [value, label] of yTicks) {
    const y = py(frame, value)
    doc.line(frame.left, y, frame.left + frame.width, y)
    doc.text(label, frame.left - 1.5, y + 1, { align: 'right' })
  }
  for (const [value, label] of xTicks) {
    doc.text(label, px(frame, value), frame.top + frame.height + 3.6, { align: 'center' })
  }
  doc.setTextColor(0)
  doc.setDrawColor(120)
  doc.setLineWidth(0.2)
  doc.rect(frame.left, frame.top, frame.width, frame.height)
}

function niceTicks(min: number, max: number, count: number): number[] {
  const step = (max - min) / count
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(step, 1e-9)))
  const nice = [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= step)!
  const ticks: number[] = []
  for (let value = Math.ceil(min / nice) * nice; value <= max + 1e-9; value += nice) {
    ticks.push(Number(value.toPrecision(6)))
  }
  return ticks
}

function polyline(doc: jsPDF, points: Array<[number, number]>) {
  for (let index = 1; index < points.length; index++) {
    doc.line(points[index - 1][0], points[index - 1][1], points[index][0], points[index][1])
  }
}

function drawHourlyTrend(doc: jsPDF, metrics: HolterMetrics, frameTop: number) {
  const hours = metrics.hourly.filter((hour) => hour.avgBpm !== null)
  if (hours.length === 0) return
  const values = hours.flatMap((hour) =>
    [hour.minBpm, hour.avgBpm, hour.maxBpm].filter((v): v is number => v !== null),
  )
  const yMin = Math.max(0, Math.floor((Math.min(...values) - 10) / 10) * 10)
  const yMax = Math.ceil((Math.max(...values) + 10) / 10) * 10
  const first = hours[0].hourStartEpochMs
  const last = hours.at(-1)!.hourStartEpochMs + 3_600_000
  const frame: Frame = {
    left: MARGIN + 12,
    top: frameTop,
    width: CONTENT_WIDTH - 14,
    height: 58,
    xMin: first,
    xMax: last,
    yMin,
    yMax,
  }
  const every = Math.max(1, Math.ceil(hours.length / 12))
  const xTicks: Array<[number, string]> = []
  for (let time = first; time <= last; time += 3_600_000 * every) {
    xTicks.push([time, formatHour(time)])
  }
  axes(
    doc,
    frame,
    'Tendencia horaria de la FC (lpm)',
    xTicks,
    niceTicks(yMin, yMax, 5).map((value) => [value, String(value)]),
  )
  const series: Array<[keyof (typeof hours)[number], [number, number, number], number]> = [
    ['maxBpm', [190, 70, 70], 0.3],
    ['avgBpm', [...INK], 0.45],
    ['minBpm', [70, 120, 190], 0.3],
  ]
  for (const [key, color, width] of series) {
    doc.setDrawColor(...color)
    doc.setLineWidth(width)
    const points = hours
      .filter((hour) => hour[key] !== null)
      .map((hour): [number, number] => [
        px(frame, hour.hourStartEpochMs + 1_800_000),
        py(frame, hour[key] as number),
      ])
    polyline(doc, points)
    doc.setFillColor(...color)
    for (const [x, y] of points) doc.circle(x, y, 0.45, 'F')
  }
  doc.setFontSize(6.6)
  const legend: Array<[string, readonly [number, number, number]]> = [
    ['Máxima', [190, 70, 70]],
    ['Media', INK],
    ['Mínima', [70, 120, 190]],
  ]
  legend.forEach(([label, color], index) => {
    const x = frame.left + frame.width - 60 + index * 20
    doc.setDrawColor(...color)
    doc.setLineWidth(0.6)
    doc.line(x, frame.top - 3.4, x + 5, frame.top - 3.4)
    doc.text(label, x + 6.5, frame.top - 2.4)
  })
}

function drawRrHistogram(doc: jsPDF, metrics: HolterMetrics, frameTop: number) {
  const histogram = metrics.rrHistogram
  if (!histogram || histogram.counts.every((count) => count === 0)) return
  const end = histogram.startMs + histogram.binMs * histogram.counts.length
  const peak = Math.max(...histogram.counts)
  const frame: Frame = {
    left: MARGIN + 12,
    top: frameTop,
    width: (CONTENT_WIDTH - 14 - 14) / 2,
    height: 55,
    xMin: histogram.startMs,
    xMax: end,
    yMin: 0,
    yMax: peak * 1.1,
  }
  axes(
    doc,
    frame,
    'Histograma de intervalos RR (ms)',
    niceTicks(histogram.startMs, end, 5).map((value) => [value, String(value)]),
    niceTicks(0, peak * 1.1, 4).map((value) => [value, formatMetricValue(value)]),
  )
  doc.setFillColor(...INK)
  histogram.counts.forEach((count, index) => {
    if (count === 0) return
    const x0 = px(frame, histogram.startMs + index * histogram.binMs)
    const x1 = px(frame, histogram.startMs + (index + 1) * histogram.binMs)
    const top = py(frame, count)
    doc.rect(x0 + 0.15, top, Math.max(0.1, x1 - x0 - 0.3), frame.top + frame.height - top, 'F')
  })
}

function drawSpectrum(doc: jsPDF, metrics: HolterMetrics, frameTop: number) {
  const frequency = metrics.hrvFrequency
  if (!frequency || frequency.spectrum.frequenciesHz.length < 2) return
  const { frequenciesHz, powerMs2PerHz } = frequency.spectrum
  const peak = Math.max(...powerMs2PerHz, 1)
  const width = (CONTENT_WIDTH - 14 - 14) / 2
  const frame: Frame = {
    left: MARGIN + 12 + width + 14,
    top: frameTop,
    width,
    height: 55,
    xMin: 0,
    xMax: 0.4,
    yMin: 0,
    yMax: peak * 1.1,
  }
  const bands: Array<[string, number, number, [number, number, number]]> = [
    ['VLF', 0.0033, 0.04, [241, 236, 226]],
    ['LF', 0.04, 0.15, [226, 236, 246]],
    ['HF', 0.15, 0.4, [230, 244, 233]],
  ]
  for (const [, from, to, color] of bands) {
    doc.setFillColor(...color)
    doc.rect(px(frame, from), frame.top, px(frame, to) - px(frame, from), frame.height, 'F')
  }
  axes(
    doc,
    frame,
    'Espectro de la VFC (ms²/Hz)',
    [0, 0.1, 0.2, 0.3, 0.4].map((value) => [value, formatMetricValue(value, 1)]),
    niceTicks(0, peak * 1.1, 4).map((value) => [value, formatMetricValue(value)]),
  )
  doc.setFontSize(6.4)
  doc.setTextColor(90)
  for (const [label, from, to] of bands) {
    doc.text(label, (px(frame, from) + px(frame, to)) / 2, frame.top + 3.5, { align: 'center' })
  }
  doc.setTextColor(0)
  doc.setDrawColor(...INK)
  doc.setLineWidth(0.35)
  polyline(
    doc,
    frequenciesHz.map((value, index): [number, number] => [
      px(frame, value),
      py(frame, powerMs2PerHz[index]),
    ]),
  )
  doc.setFontSize(6.6)
  doc.text(
    `LF/HF: ${formatMetricValue(frequency.lfHfRatio, 2)} · ventanas de 5 min: ${frequency.windows}`,
    frame.left,
    frame.top + frame.height + 7.5,
  )
}

export function hasTrends(metrics: HolterMetrics | null | undefined): metrics is HolterMetrics {
  return metrics?.status === 'ok'
}

/** Página 2: tendencia horaria, histograma RR y espectro, con la nota de método. */
export function drawTrendsPage(doc: jsPDF, metrics: HolterMetrics, startY: number) {
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(13)
  doc.setTextColor(...INK)
  doc.text('Tendencias y variabilidad', MARGIN, startY)
  doc.setTextColor(0)

  drawHourlyTrend(doc, metrics, startY + 12)
  drawRrHistogram(doc, metrics, startY + 92)
  drawSpectrum(doc, metrics, startY + 92)

  const analysis = metrics.analysis
  const heart = metrics.heartRate
  const method = [
    'Método. Los latidos se detectan en el servidor (Pan-Tompkins) sobre el canal 1. Los tramos con electrodo suelto, señal no analizable o saturación se excluyen, y un corte o hueco del registro nunca se cuenta como pausa.',
    `FC mínima y máxima: promedio móvil de ${heart?.windowBeats ?? 8} intervalos NN consecutivos. La VFC se calcula sobre intervalos RR filtrados (300–2000 ms y ±20 % de la mediana local): todavía no hay clasificación de latidos, así que los ectópicos se descartan por ese filtro y no por su morfología.`,
    analysis
      ? `Tiempo analizado: ${formatDuration(analysis.analyzedMs)} · excluido por calidad: ${formatDuration(analysis.excludedMs)} · intervalos RR válidos: ${formatMetricValue(analysis.rrIntervals)} · NN: ${formatMetricValue(analysis.nnIntervals)} · algoritmo v${analysis.algorithmVersion}.`
      : '',
    metrics.pauses?.items.length
      ? `Pausas registradas: ${metrics.pauses.items
          .map(
            (item) =>
              `${formatAxisTime(item.epochMs)} (${formatMetricValue((item.durationMs ?? 0) / 1000, 2, 's')})`,
          )
          .join(' · ')}.`
      : '',
  ].filter(Boolean)
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(7.6)
  let y = startY + 172
  for (const paragraph of method) {
    const lines = doc.splitTextToSize(paragraph, CONTENT_WIDTH)
    doc.text(lines, MARGIN, y)
    y += lines.length * 3.6 + 2
  }
}
