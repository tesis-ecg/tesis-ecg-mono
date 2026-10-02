// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Study, StudyClinicalReportDraft } from '../types'

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
  updateError: null as unknown,
}))

const draft: StudyClinicalReportDraft = {
  studyId: 'study-1',
  revision: 4,
  indication: 'Palpitaciones',
  medications: null,
  referringProfessional: null,
  technician: null,
  clinicalObservations: null,
  conclusion: 'Ritmo evaluado.',
  updatedAt: '2026-09-19T12:00:00Z',
  updatedBy: 'user-1',
  updatedByName: 'Dra. Ana',
  updatedByRole: 'medico',
}

vi.mock('../hooks/useStudyClinicalReport', () => ({
  useStudyClinicalReportDraft: () => ({
    data: draft,
    isLoading: false,
    isError: false,
    refetch: mocks.refetch,
  }),
  useStudyClinicalReportPreview: () => ({
    data: {
      snapshot: {
        quality: { coveragePercent: 98, interruptionMs: 60_000 },
        findings: [],
      },
      windows: [],
      canGenerateDraft: true,
      canFinalize: true,
      blockingReasons: [],
      issues: [],
    },
    isLoading: false,
    isError: false,
  }),
  useStudyClinicalReportVersions: () => ({ data: [], isLoading: false, isError: false }),
  useUpdateStudyClinicalReportDraft: () => ({
    mutateAsync: mocks.mutateAsync,
    isPending: false,
    isError: Boolean(mocks.updateError),
    error: mocks.updateError,
  }),
}))

const study: Study = {
  id: 'study-1',
  patientId: 'patient-1',
  patientName: 'Ana Pérez',
  deviceId: 'device-1',
  startedAt: '2026-09-19T11:00:00Z',
  endedAt: '2026-09-19T12:00:00Z',
  durationMs: 3_600_000,
  deviceSerial: 'HOL-1',
  canAccessDevice: true,
  lastDataReceivedAt: null,
  status: 'completed',
}

import { StudyClinicalReportTab } from './StudyClinicalReportTab'

describe('StudyClinicalReportTab', () => {
  beforeEach(() => {
    mocks.updateError = null
    mocks.mutateAsync.mockReset().mockResolvedValue({ ...draft, revision: 5 })
    mocks.refetch.mockReset()
  })

  afterEach(cleanup)

  it('guarda el borrador con la revisión que estaba editando', async () => {
    render(<StudyClinicalReportTab study={study} onPreview={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Indicación del estudio *'), {
      target: { value: 'Síncope durante ejercicio' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }))

    await waitFor(() =>
      expect(mocks.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ revision: 4, indication: 'Síncope durante ejercicio' }),
      ),
    )
  })

  it('guarda cambios pendientes antes de abrir la previsualización', async () => {
    const onPreview = vi.fn()
    render(<StudyClinicalReportTab study={study} onPreview={onPreview} />)
    fireEvent.change(screen.getByLabelText('Conclusión / interpretación final *'), {
      target: { value: 'Nueva interpretación' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Previsualizar borrador' }))

    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
  })
})
