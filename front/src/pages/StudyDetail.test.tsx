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
vi.mock('@/features/ecg/components/ECGViewer', () => ({ ECGViewer: () => null }))
vi.mock('@/features/ecg/components/ECGMinimap', () => ({ ECGMinimap: () => null }))
vi.mock('@/features/ecg/components/ECGFullscreenDialog', () => ({
  ECGFullscreenDialog: () => null,
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
})
