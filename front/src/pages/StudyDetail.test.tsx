// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import type { ECGAnnotation } from '@/features/ecg/types'
import type { Study, StudyFindings } from '@/features/studies/types'

const hooks = vi.hoisted(() => ({
  study: vi.fn(),
  ecg: vi.fn(),
  reports: vi.fn(),
  findings: vi.fn(),
  metrics: vi.fn(),
  focus: vi.fn(),
}))

vi.mock('@/features/studies/hooks/useStudy', () => ({ useStudy: hooks.study }))
vi.mock('@/features/ecg/hooks/useEcgSignal', () => ({ useEcgSignal: hooks.ecg }))
vi.mock('@/features/studies/hooks/useStudyPatientReports', () => ({
  useStudyPatientReports: hooks.reports,
}))
vi.mock('@/features/studies/hooks/useStudyFindings', () => ({
  useStudyFindings: hooks.findings,
}))
vi.mock('@/features/studies/hooks/useHolterMetrics', () => ({
  useHolterMetrics: hooks.metrics,
}))
// Solo el salto del visor: el resto del catálogo (rótulos, severidades) es el real.
vi.mock('@/features/ecg/annotationMeta', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/ecg/annotationMeta')>()),
  focusViewerOnAnnotation: hooks.focus,
}))
vi.mock('@/features/ecg/components/ECGViewer', () => ({
  ECGViewer: ({
    paperSpeed,
    amplitude,
    initialWindowSeconds,
  }: {
    paperSpeed: number
    amplitude: number
    initialWindowSeconds?: number
  }) => (
    <output
      data-testid="study-ecg-viewer"
      data-paper-speed={paperSpeed}
      data-amplitude={amplitude}
      data-initial-window={initialWindowSeconds}
    />
  ),
}))
vi.mock('@/features/ecg/components/ECGMinimap', () => ({ ECGMinimap: () => null }))
vi.mock('@/features/ecg/components/ECGFullscreenDialog', () => ({
  ECGFullscreenDialog: () => null,
}))
vi.mock('@/features/ecg/components/ECGClinicalReportDialog', () => ({
  ECGClinicalReportDialog: () => null,
}))

import { StudyDetail } from './StudyDetail'

const study: Study = {
  id: 'study-1',
  patientId: 'patient-1',
  patientName: 'Ana Pérez',
  deviceId: 'device-1',
  startedAt: '2026-09-16T12:00:00Z',
  endedAt: null,
  durationMs: 0,
  deviceSerial: 'HOL-001',
  canAccessDevice: true,
  lastDataReceivedAt: null,
  status: 'scheduled',
  doctorId: 'doctor-1',
  doctorName: 'Dra. Test',
}

beforeEach(() => {
  hooks.study.mockReturnValue({
    data: study,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  hooks.ecg.mockReturnValue({
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  hooks.reports.mockReturnValue({
    data: { items: [], total: 0, pendingSignalTotal: 0 },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  hooks.findings.mockReturnValue({
    data: findings,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  hooks.metrics.mockReturnValue({
    data: {
      status: 'pending',
      unavailableReason: 'ANALYSIS_PENDING',
      analysis: null,
      heartRate: null,
      pauses: null,
      supraventricular: null,
      ventricular: null,
      ectopyUnavailableReason: null,
      hrvTime: null,
      hrvFrequency: null,
      st: [],
      hourly: [],
      rrHistogram: null,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  hooks.focus.mockReset()
})

afterEach(cleanup)

const T0 = 1_700_000_000_000

const tachycardia: ECGAnnotation = {
  id: 'finding-1',
  kind: 'tachycardia',
  category: 'clinical',
  severity: 'high',
  startMs: T0 + 10_000,
  endMs: T0 + 20_000,
  confidenceScore: 0.9,
  linkedAnnotationId: null,
  description: null,
}

const findings: StudyFindings = {
  studyId: 'study-1',
  sampleRate: 1,
  sampleCount: 60,
  durationMs: 60_000,
  modelVersion: 'ml-1',
  quality: {
    analyzableRatio: 1,
    goodRatio: 1,
    marginalRatio: 0,
    badRatio: 0,
    evaluatedMs: 60_000,
    intervals: [],
  },
  groups: [
    {
      key: 'kind:tachycardia',
      kind: 'tachycardia',
      category: 'clinical',
      severity: 'high',
      occurrences: 1,
      firstOffsetMs: 10_000,
      lastOffsetMs: 20_000,
      firstEpochMs: T0 + 10_000,
      lastEpochMs: T0 + 20_000,
      items: [
        {
          id: 'finding-1',
          kind: 'tachycardia',
          category: 'clinical',
          severity: 'high',
          startOffsetMs: 10_000,
          endOffsetMs: 20_000,
          startEpochMs: T0 + 10_000,
          endEpochMs: T0 + 20_000,
          confidenceScore: 0.9,
          modelVersion: 'ml-1',
          validationStatus: 'pending',
        },
      ],
    },
  ],
  ungrouped: [],
  totals: { tachycardia: 1 },
  truncated: false,
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/studies/study-1']}>
      <Routes>
        <Route path="/studies/:id" element={<StudyDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('StudyDetail device tab', () => {
  it('muestra la solapa cuando el usuario todavía puede acceder al Holter', () => {
    renderPage()

    expect(screen.getByRole('tab', { name: 'Dispositivo' })).toBeTruthy()
  })

  it('oculta la solapa cuando el Holter fue transferido a otro médico', () => {
    hooks.study.mockReturnValue({
      data: { ...study, canAccessDevice: false },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    renderPage()

    expect(screen.queryByRole('tab', { name: 'Dispositivo' })).toBeNull()
  })

  it('abre la señal a escala clínica 25/20 sin una ventana temporal fija', () => {
    hooks.study.mockReturnValue({
      data: { ...study, durationMs: 60_000, status: 'completed' },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })
    hooks.ecg.mockReturnValue({
      data: {
        sampleRate: 1,
        durationMs: 60_000,
        samples: new Float32Array(60),
        startTimestamp: 1_700_000_000_000,
        timestampsMs: new Float64Array(60),
        gapIndices: [],
        timeline: [],
        annotations: [],
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    renderPage()

    const viewer = screen.getByTestId('study-ecg-viewer')
    expect(viewer.getAttribute('data-paper-speed')).toBe('25')
    expect(viewer.getAttribute('data-amplitude')).toBe('20')
    expect(viewer.hasAttribute('data-initial-window')).toBe(false)
  })
})

describe('StudyDetail analysis tab', () => {
  it('al tocar un hallazgo vuelve a la señal y centra el visor en su banda', async () => {
    hooks.study.mockReturnValue({
      data: { ...study, durationMs: 60_000, status: 'completed' },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })
    hooks.ecg.mockReturnValue({
      data: {
        sampleRate: 1,
        durationMs: 60_000,
        samples: new Float32Array(60),
        startTimestamp: T0,
        timestampsMs: new Float64Array(60),
        gapIndices: [],
        timeline: [],
        annotations: [tachycardia],
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    renderPage()

    // Radix cambia de solapa con `mousedown`, no con `click`.
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Análisis' }), { button: 0 })
    fireEvent.click(screen.getByRole('button', { name: /^Ver Taquicardia del/ }))

    expect(screen.getByRole('tab', { name: 'Señal ECG' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    // La banda que ya viajó en la señal, no la copia armada desde `/findings`:
    // es la que el visor y el panel saben resaltar.
    await waitFor(() => expect(hooks.focus).toHaveBeenCalledOnce())
    expect(hooks.focus.mock.calls[0][1]).toBe(tachycardia)
  })

  it('centra el visor en la evidencia de una métrica aunque no sea una banda', async () => {
    hooks.study.mockReturnValue({
      data: { ...study, durationMs: 60_000, status: 'completed' },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })
    hooks.metrics.mockReturnValue({
      data: {
        status: 'ok',
        unavailableReason: null,
        analysis: {
          algorithmVersion: 2,
          analyzedUntilSample: 60,
          analyzedMs: 60_000,
          excludedMs: 0,
          rrIntervals: 70,
          nnIntervals: 70,
        },
        heartRate: {
          averageBpm: 70,
          min: { value: 52, sampleIndex: 30, epochMs: T0 + 30_000 },
          max: null,
          totalBeats: 71,
          abnormalBeats: null,
          abnormalPerThousand: null,
          windowBeats: 8,
        },
        pauses: { thresholdMs: 2000, count: 0, longest: null, items: [] },
        supraventricular: null,
        ventricular: null,
        ectopyUnavailableReason: null,
        hrvTime: null,
        hrvFrequency: null,
        st: [],
        hourly: [],
        rrHistogram: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    renderPage()

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Análisis' }), { button: 0 })
    fireEvent.click(screen.getByRole('button', { name: 'Ver FC mínima en el ECG' }))

    expect(screen.getByRole('tab', { name: 'Señal ECG' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await waitFor(() => expect(hooks.focus).toHaveBeenCalledOnce())
    expect(hooks.focus.mock.calls[0][1]).toMatchObject({
      kind: 'hr_min',
      startMs: T0 + 30_000,
      endMs: T0 + 30_000,
    })
  })
})
