import { describe, expect, it } from 'vitest'

import { automaticAnnotations, buildClinicalReport } from './clinicalReport'
import { detailWindowRequests, estimateOverviewPages } from './clinicalReportPlanning'
import type { ClinicalReportInput } from './clinicalReportTypes'

const start = 1_700_000_000_000

function input(): ClinicalReportInput {
  return {
    study: {
      id: 'study-1',
      patientId: 'patient-1',
      patientName: 'Ana Pérez',
      deviceId: 'device-1',
      startedAt: new Date(start).toISOString(),
      endedAt: new Date(start + 20_000).toISOString(),
      durationMs: 20_000,
      deviceSerial: 'HOL-001',
      canAccessDevice: true,
      lastDataReceivedAt: null,
      status: 'completed',
    },
    patient: {
      id: 'patient-1',
      fullName: 'Ana Pérez',
      dni: '12345678',
      birthDate: '1980-01-01',
      sex: 'F',
      assignedDeviceId: 'device-1',
      assignedDeviceSerial: 'HOL-001',
      studyStatus: 'completed',
      lastDataReceivedAt: null,
      contactEmail: null,
      contactPhone: null,
      hasAppAccount: true,
    },
    signal: {
      sampleRate: 2,
      durationMs: 20_000,
      samples: Float32Array.from({ length: 40 }, (_, index) => Math.sin(index / 3)),
      startTimestamp: start,
      timestampsMs: Float64Array.from({ length: 40 }, (_, index) => start + index * 500),
      gapIndices: [],
      timeline: [],
      annotations: [
        {
          id: 'finding-1',
          kind: 'tachycardia',
          category: 'clinical',
          severity: 'high',
          startMs: start + 5_000,
          endMs: start + 5_200,
          confidenceScore: 0.9,
          linkedAnnotationId: null,
          description: null,
        },
      ],
      metadata: {
        formatVersion: 3,
        encoding: 'float32-le',
        sampleCount: 40,
        isSimulated: false,
        overviewSamplesPerBucket: null,
      },
    },
    reports: [
      {
        id: 'report-1',
        occurredAt: new Date(start + 5_500).toISOString(),
        source: 'manual',
        symptoms: ['palpitations'],
        symptomLabels: ['Palpitaciones'],
        symptomsOther: null,
        activity: 'rest',
        activityLabel: 'Reposo',
        activityOther: null,
        notes: null,
        alertId: null,
        alertKind: null,
        createdAt: new Date(start + 5_500).toISOString(),
        offsetMs: 5_500,
        visibleInChart: true,
      },
    ],
    detailWindows: [],
    sectionMinutes: 1,
    paperSpeed: 25,
    amplitude: 10,
    generatedAt: new Date(start + 20_000).toISOString(),
  }
}

describe('clinical ECG report', () => {
  it('deduplicates overlapping finding and patient-report windows', () => {
    const value = input()
    // Una respuesta a una alerta puede enviarse bastante después del evento;
    // el detalle debe seguir el ancla visible del gráfico, no esa hora tardía.
    value.reports[0].occurredAt = new Date(start + 19_000).toISOString()
    const windows = detailWindowRequests(value.signal, value.reports)

    expect(windows).toHaveLength(1)
    expect(windows[0].startEpochMs).toBe(start + 100)
    expect(windows[0].endEpochMs).toBe(start + 10_500)
  })

  it('estimates overview pages and generates a PDF document', () => {
    const value = input()

    expect(estimateOverviewPages(value.signal, 1)).toBe(1)
    const pdf = buildClinicalReport(value)
    expect(new TextDecoder().decode(pdf.slice(0, 8))).toContain('%PDF-')
  })

  it('does not classify patient markers as automatic findings', () => {
    const value = input()
    value.signal.annotations.push({
      id: 'patient-marker-1',
      kind: 'patient_report',
      category: 'patient_marker',
      severity: 'low',
      startMs: start + 6_000,
      endMs: start + 6_000,
      confidenceScore: null,
      linkedAnnotationId: null,
      description: 'Palpitaciones',
    })

    expect(automaticAnnotations(value.signal.annotations)).toEqual([value.signal.annotations[0]])
  })
})
