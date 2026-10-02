import { describe, expect, it } from 'vitest'

import { automaticAnnotations, buildClinicalReport } from './clinicalReport'
import type { ClinicalReportInput } from './clinicalReportTypes'

const start = 1_700_000_000_000

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
