// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import type { Study } from '@/features/studies/types'

const hooks = vi.hoisted(() => ({
  study: vi.fn(),
  ecg: vi.fn(),
  reports: vi.fn(),
}))

vi.mock('@/features/studies/hooks/useStudy', () => ({ useStudy: hooks.study }))
vi.mock('@/features/ecg/hooks/useEcgSignal', () => ({ useEcgSignal: hooks.ecg }))
vi.mock('@/features/studies/hooks/useStudyPatientReports', () => ({
  useStudyPatientReports: hooks.reports,
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
})

afterEach(cleanup)

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
