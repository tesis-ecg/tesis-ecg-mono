import { describe, expect, it } from 'vitest'

import type { HolterMetrics } from '@/features/studies/types'

import { automaticAnnotations, buildClinicalReport } from './clinicalReport'
import { formatMetricValue, sexLabel } from './clinicalReportFormat'
import { metricsMethodNote, metricsStatusText } from './clinicalReportSummary'
import type { ClinicalReportInput } from './clinicalReportTypes'

const start = 1_700_000_000_000

function evidence(value: number, offsetMs: number, durationMs: number | null = null) {
  return { value, sampleIndex: offsetMs / 2, epochMs: start + offsetMs, durationMs }
}

function metrics(): HolterMetrics {
  const stKind = { episodes: 0, durationSeconds: 0, maxDeviation: null, maxSlopeMvPerMin: null }
  return {
    status: 'ok',
    unavailableReason: null,
    analysis: {
      algorithmVersion: 1,
      analyzedUntilSample: 3_600_000,
      analyzedMs: 7_100_000,
      excludedMs: 100_000,
      rrIntervals: 8_000,
      nnIntervals: 7_900,
    },
    heartRate: {
      averageBpm: 72,
      min: evidence(55, 3_000_000),
      max: evidence(118, 5_000_000),
      totalBeats: 8_520,
      abnormalBeats: null,
      abnormalPerThousand: null,
      windowBeats: 8,
    },
    pauses: {
      thresholdMs: 2000,
      count: 2,
      longest: evidence(2600, 4_000_000, 2600),
      items: [evidence(2600, 4_000_000, 2600), evidence(2100, 6_000_000, 2100)],
    },
    supraventricular: null,
    ventricular: null,
    ectopyUnavailableReason: 'BEAT_CLASSIFICATION_UNAVAILABLE',
    hrvTime: { sdnnMs: 84.1, sdannMs: 69, rmssdMs: 50.5, pnn50Percent: 2, cv: 0.05, meanNnMs: 830 },
    hrvFrequency: {
      totalPowerMs2: 3271,
      ulfMs2: 2073.4,
      vlfMs2: 779.6,
      lfMs2: 172.5,
      hfMs2: 245.6,
      lfHfRatio: 0.7,
      windows: 24,
      spectrum: {
        frequenciesHz: Array.from({ length: 120 }, (_, index) => index / 300),
        powerMs2PerHz: Array.from({ length: 120 }, (_, index) => 4000 / (1 + index)),
      },
    },
    st: [
      {
        channel: 1,
        label: 'Canal 1 (LL-RA)',
        analyzedMinutes: 118,
        medianLevelMv: 0.02,
        elevation: {
          ...stKind,
          episodes: 1,
          durationSeconds: 120,
          maxSlopeMvPerMin: 0.12,
          maxDeviation: evidence(0.18, 2_000_000),
        },
        depression: stKind,
      },
    ],
    hourly: [
      { hourStartEpochMs: start, beats: 4300, avgBpm: 74, minBpm: 60, maxBpm: 118 },
      { hourStartEpochMs: start + 3_600_000, beats: 4220, avgBpm: 70, minBpm: 55, maxBpm: 96 },
    ],
    rrHistogram: {
      startMs: 300,
      binMs: 50,
      counts: Array.from({ length: 34 }, (_, index) => Math.max(0, 400 - (index - 10) ** 2 * 6)),
    },
  }
}

function input(): ClinicalReportInput {
  const draft = {
    studyId: 'study-1',
    revision: 2,
    indication: 'Palpitaciones',
    medications: 'Sin medicación',
    referringProfessional: 'Dra. Ejemplo',
    technician: 'Técnico Ejemplo',
    clinicalObservations: null,
    conclusion: 'Interpretación de prueba.',
    updatedAt: new Date(start).toISOString(),
    updatedBy: 'user-1',
    updatedByName: 'Dr. Ejemplo',
    updatedByRole: 'medico',
  }
  return {
    snapshot: {
      schemaVersion: 1,
      version: 1,
      study: {
        id: 'study-1',
        status: 'completed',
        startedAt: new Date(start).toISOString(),
        endedAt: new Date(start + 60_000).toISOString(),
        durationMs: 60_000,
        deviceSerial: 'HOL-001',
        sampleRate: 500,
        isSimulated: false,
      },
      patient: {
        id: 'patient-1',
        fullName: 'Ana Pérez',
        dni: '12345678',
        birthDate: '1980-01-01',
        sex: 'F',
        medicalRecordNumber: null,
      },
      responsibleDoctor: {
        fullName: 'Dr. Ejemplo',
        specialty: 'Cardiología',
        licenseNumber: 'MN 123',
      },
      clinicalContext: draft,
      quality: {
        recordedMs: 59_000,
        wallClockMs: 60_000,
        interruptionMs: 1_000,
        coveragePercent: 98.3,
        segments: 2,
        cuts: 1,
        lastDataReceivedAt: new Date(start + 60_000).toISOString(),
        synchronizationSources: ['ntp'],
        maxSynchronizationUncertaintyMs: 20,
      },
      findings: [
        {
          kind: 'tachycardia',
          count: 1,
          severities: ['high'],
          totalDurationMs: 10_000,
          longestDurationMs: 10_000,
          symptomaticCount: 1,
        },
      ],
      technicalEvents: [],
      patientReports: [],
      selectedWindows: [],
    },
    windowPlans: [],
    detailWindows: [],
    documentStatus: 'draft',
    generatedAt: new Date(start + 60_000).toISOString(),
    generatedBy: { fullName: 'Dr. Ejemplo', role: 'medico' },
  }
}

describe('clinical ECG report', () => {
  it('genera un PDF aunque no existan hallazgos elegibles', () => {
    const pdf = buildClinicalReport(input())

    expect(new TextDecoder().decode(pdf.slice(0, 8))).toContain('%PDF-')
  })

  it('genera la hoja resumen y la de tendencias con métricas completas', () => {
    const value = input()
    value.snapshot.schemaVersion = 2
    value.snapshot.metrics = metrics()
    value.windowPlans = [
      {
        id: 'metric:pause_longest',
        findingId: null,
        kind: 'pause_longest',
        category: 'metric',
        severity: 'medium',
        findingStartEpochMs: start + 4_000_000,
        findingEndEpochMs: start + 4_002_600,
        findingDurationMs: 2_600,
        startEpochMs: start + 3_998_000,
        endEpochMs: start + 4_004_600,
        blockIndex: 1,
        blockCount: 1,
        confidenceScore: null,
        description: null,
        relatedSymptoms: [],
      },
    ]
    value.detailWindows = [
      {
        id: 'metric:pause_longest',
        startEpochMs: start + 3_998_000,
        endEpochMs: start + 4_004_600,
        timestampsMs: Array.from({ length: 66 }, (_, index) => start + 3_998_000 + index * 100),
        samplesMv: Array.from({ length: 66 }, (_, index) => Math.sin(index / 3)),
        gapIndices: [],
        source: 'raw',
      },
    ]

    const pdf = buildClinicalReport(value)

    expect(new TextDecoder().decode(pdf.slice(0, 8))).toContain('%PDF-')
  })

  it('genera el resumen aunque las métricas no estén disponibles', () => {
    const pending = input()
    pending.snapshot.metrics = { ...metrics(), status: 'pending', heartRate: null, hrvTime: null }
    const missing = input()
    missing.snapshot.metrics = null

    for (const value of [pending, missing]) {
      expect(new TextDecoder().decode(buildClinicalReport(value).slice(0, 8))).toContain('%PDF-')
    }
  })

  it('genera tiras desde las ventanas crudas planificadas', () => {
    const value = input()
    value.windowPlans = [
      {
        id: 'finding:finding-1:1',
        findingId: 'finding-1',
        kind: 'tachycardia',
        category: 'clinical',
        severity: 'high',
        findingStartEpochMs: start + 5_000,
        findingEndEpochMs: start + 15_000,
        findingDurationMs: 10_000,
        startEpochMs: start + 5_000,
        endEpochMs: start + 15_000,
        blockIndex: 1,
        blockCount: 1,
        confidenceScore: 0.9,
        description: 'Palpitaciones',
        relatedSymptoms: ['Mareo'],
      },
    ]
    value.detailWindows = [
      {
        id: 'finding:finding-1:1',
        startEpochMs: start + 5_000,
        endEpochMs: start + 15_000,
        timestampsMs: Array.from({ length: 100 }, (_, index) => start + 5_000 + index * 100),
        samplesMv: Array.from({ length: 100 }, (_, index) => Math.sin(index / 4)),
        gapIndices: [50],
        source: 'raw',
      },
    ]

    expect(new TextDecoder().decode(buildClinicalReport(value).slice(0, 8))).toContain('%PDF-')
  })

  it('sólo clasifica como automáticos los hallazgos clínicos no vinculados', () => {
    const clinical = {
      id: 'finding-1',
      kind: 'tachycardia',
      category: 'clinical' as const,
      severity: 'high' as const,
      startMs: start,
      endMs: start + 1000,
      confidenceScore: 0.9,
      linkedAnnotationId: null,
      description: null,
    }
    const patientMarker = {
      ...clinical,
      id: 'marker-1',
      category: 'patient_marker' as const,
      kind: 'patient_report',
    }

    expect(automaticAnnotations([clinical, patientMarker])).toEqual([clinical])
  })
})

describe('formato de métricas del informe', () => {
  it('distingue "no calculado" de cero y usa coma decimal', () => {
    expect(formatMetricValue(null)).toBe('N/D')
    expect(formatMetricValue(undefined, 1)).toBe('N/D')
    expect(formatMetricValue(0)).toBe('0')
    expect(formatMetricValue(84.06, 1, 'ms')).toBe('84,1 ms')
    expect(formatMetricValue(100624)).toBe('100.624')
  })

  it('muestra el sexo con su nombre y no con el código', () => {
    expect(sexLabel('F')).toBe('Femenino')
    expect(sexLabel('M')).toBe('Masculino')
    expect(sexLabel('X')).toBe('No binario')
  })

  it('explica por qué faltan las métricas', () => {
    expect(metricsStatusText(metrics())).toBeNull()
    expect(metricsStatusText({ ...metrics(), status: 'pending' })).toMatch(/pendiente/)
    expect(metricsStatusText(null)).toMatch(/no incluidas/)
  })

  it('describe el método del algoritmo que calculó las métricas congeladas', () => {
    // Un informe final ya emitido con la versión 1 se vuelve a generar desde su
    // snapshot: tiene que seguir diciendo exactamente lo que decía.
    expect(metricsMethodNote(1)).toBe(
      'Método. Los latidos se detectan en el servidor (Pan-Tompkins) sobre el canal 1. Los tramos con electrodo suelto, señal no analizable o saturación se excluyen, y un corte o hueco del registro nunca se cuenta como pausa.',
    )
    expect(metricsMethodNote(1)).not.toMatch(/ruido/)

    const v2 = metricsMethodNote(2)
    expect(v2).toMatch(/electrodo suelto, señal no analizable o saturación/)
    expect(v2).toMatch(/análisis automático de calidad de señal clasifica como ruido/)
    // El ruido del motor no saca pausas: una asistolia se le parece.
    expect(v2).toMatch(/pausas se buscan también en esos tramos/)
    expect(v2).toMatch(/nunca se cuenta como pausa/)
  })

  it('genera la hoja de tendencias con métricas de la versión 2 del algoritmo', () => {
    const value = input()
    value.snapshot.schemaVersion = 2
    const base = metrics()
    value.snapshot.metrics = {
      ...base,
      analysis: base.analysis && { ...base.analysis, algorithmVersion: 2 },
    }

    expect(new TextDecoder().decode(buildClinicalReport(value).slice(0, 8))).toContain('%PDF-')
  })
})
