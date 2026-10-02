// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Study, StudyClinicalReportPreview } from '@/features/studies/types'

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children, className }: { children: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

vi.mock('@/features/auth/AuthContext', () => ({
  useAuth: () => ({ user: { fullName: 'Dra. Ana', role: 'medico' } }),
}))

const preview: StudyClinicalReportPreview = {
  draft: {
    studyId: 'study-1',
    revision: 1,
    indication: 'Palpitaciones',
    medications: null,
    referringProfessional: null,
    technician: null,
    clinicalObservations: null,
    conclusion: 'Sin hallazgos relevantes.',
    updatedAt: null,
    updatedBy: null,
    updatedByName: null,
    updatedByRole: null,
  },
  snapshot: {
    schemaVersion: 1,
    version: 1,
    study: {
      id: 'study-1',
      status: 'completed',
      startedAt: '2026-09-19T12:00:00Z',
      endedAt: '2026-09-19T13:00:00Z',
      durationMs: 60_000,
      deviceSerial: 'HOLTER-1',
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
    responsibleDoctor: { fullName: 'Dra. Ana', specialty: null, licenseNumber: null },
    clinicalContext: {
      studyId: 'study-1',
      revision: 1,
      indication: 'Palpitaciones',
      medications: null,
      referringProfessional: null,
      technician: null,
      clinicalObservations: null,
      conclusion: 'Sin hallazgos relevantes.',
      updatedAt: null,
      updatedBy: null,
      updatedByName: null,
      updatedByRole: null,
    },
    quality: {
      recordedMs: 60_000,
      wallClockMs: 60_000,
      interruptionMs: 0,
      coveragePercent: 100,
      segments: 1,
      cuts: 0,
      lastDataReceivedAt: null,
      synchronizationSources: ['ntp'],
      maxSynchronizationUncertaintyMs: 0,
    },
    findings: [],
    technicalEvents: [],
    patientReports: [],
    selectedWindows: [],
  },
  snapshotHash: 'a'.repeat(64),
  windows: [],
  nextVersion: 1,
  canGenerateDraft: true,
  canFinalize: true,
  blockingReasons: [],
  issues: [],
}

const refetch = vi.fn().mockResolvedValue({ data: preview })
const finalize = vi.fn().mockResolvedValue({})
vi.mock('@/features/studies/hooks/useStudyClinicalReport', () => ({
  useStudyClinicalReportPreview: () => ({
    data: preview,
    isLoading: false,
    isError: false,
    refetch,
  }),
  useFinalizeStudyClinicalReport: () => ({
    mutateAsync: finalize,
    isPending: false,
  }),
}))

vi.mock('../api/ecgApi', () => ({
  getStudyEcgReportWindows: vi.fn().mockResolvedValue([]),
}))

import { ECGClinicalReportDialog } from './ECGClinicalReportDialog'

class WorkerMock {
  static instances: WorkerMock[] = []
  onmessage: ((event: MessageEvent<{ ok: boolean; pdf?: ArrayBuffer }>) => void) | null = null
  onerror: (() => void) | null = null
  postMessage = vi.fn()
  terminate = vi.fn()

  constructor() {
    WorkerMock.instances.push(this)
  }
}

const study: Study = {
  id: 'study-1',
  patientId: 'patient-1',
  patientName: 'Ana Pérez',
  deviceId: 'device-1',
  startedAt: '2026-09-19T12:00:00Z',
  endedAt: '2026-09-19T13:00:00Z',
  durationMs: 60_000,
  deviceSerial: 'HOLTER-1',
  canAccessDevice: true,
  lastDataReceivedAt: '2026-09-19T13:00:00Z',
  status: 'completed',
}

function renderDialog() {
  return render(<ECGClinicalReportDialog open onOpenChange={vi.fn()} study={study} />)
}

async function finishGeneration(worker: WorkerMock, bytes = 64) {
  await act(async () => {
    worker.onmessage?.({
      data: { ok: true, pdf: new ArrayBuffer(bytes) },
    } as MessageEvent<{ ok: boolean; pdf?: ArrayBuffer }>)
  })
}

describe('ECGClinicalReportDialog preview', () => {
  const createObjectURL = vi.fn()
  const revokeObjectURL = vi.fn()

  beforeEach(() => {
    WorkerMock.instances = []
    preview.canGenerateDraft = true
    preview.canFinalize = true
    preview.blockingReasons = []
    preview.issues = []
    refetch.mockClear()
    finalize.mockClear()
    createObjectURL
      .mockReset()
      .mockReturnValueOnce('blob:report-1')
      .mockReturnValueOnce('blob:report-2')
    revokeObjectURL.mockReset()
    vi.stubGlobal('Worker', WorkerMock)
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('reemplaza el skeleton por el visor cuando termina la generación', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' }))
    await waitFor(() => expect(WorkerMock.instances).toHaveLength(1))
    expect(screen.getByTestId('clinical-report-preview-skeleton')).toBeTruthy()

    await finishGeneration(WorkerMock.instances[0])

    expect((await screen.findByTestId('clinical-report-pdf-preview')).getAttribute('data')).toBe(
      'blob:report-1',
    )
    expect(screen.getByRole('button', { name: 'Regenerar borrador' })).toBeTruthy()
  })

  it('retira el skeleton al cancelar', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' }))
    await waitFor(() => expect(WorkerMock.instances).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))

    await waitFor(() => expect(screen.queryByTestId('clinical-report-preview-skeleton')).toBeNull())
    expect(WorkerMock.instances[0].terminate).toHaveBeenCalled()
  })

  it('genera en el hilo principal si el worker no puede iniciarse', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' }))
    await waitFor(() => expect(WorkerMock.instances).toHaveLength(1))

    await act(async () => {
      WorkerMock.instances[0].onerror?.()
    })

    expect((await screen.findByTestId('clinical-report-pdf-preview')).getAttribute('data')).toBe(
      'blob:report-1',
    )
    expect(WorkerMock.instances[0].terminate).toHaveBeenCalled()
  })

  it('genera en el hilo principal si el constructor del worker falla', async () => {
    class FailingWorker {
      constructor() {
        throw new DOMException('Blocked by Content Security Policy', 'SecurityError')
      }
    }
    vi.stubGlobal('Worker', FailingWorker)
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' }))

    expect((await screen.findByTestId('clinical-report-pdf-preview')).getAttribute('data')).toBe(
      'blob:report-1',
    )
    expect(WorkerMock.instances).toHaveLength(0)
  })

  it('genera el documento final y lo persiste', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Generar informe final' }))
    await waitFor(() => expect(WorkerMock.instances).toHaveLength(1))
    await finishGeneration(WorkerMock.instances[0])

    await waitFor(() => expect(finalize).toHaveBeenCalledTimes(1))
    expect(finalize.mock.calls[0][0]).toMatchObject({
      draftRevision: 1,
      snapshotHash: 'a'.repeat(64),
    })
  })

  it('deshabilita la generación final ante requisitos faltantes y muestra el bloqueo', () => {
    preview.canFinalize = false
    preview.blockingReasons = ['Completá la conclusión clínica.']
    preview.issues = [
      {
        code: 'MISSING_CONCLUSION',
        message: 'Completá la conclusión clínica.',
        severity: 'blocking',
      },
    ]
    renderDialog()

    expect(
      screen.getByRole('button', { name: 'Generar informe final' }).hasAttribute('disabled'),
    ).toBe(true)
    expect(screen.getByText('Faltan datos para generar la versión final')).toBeTruthy()
  })

  it('muestra la simulación como advertencia sin bloquear la generación', () => {
    preview.issues = [
      {
        code: 'SIMULATED_STUDY',
        message: 'La señal fue generada con un chaleco simulado.',
        severity: 'warning',
      },
    ]
    renderDialog()

    expect(
      screen.getByRole('button', { name: 'Generar informe final' }).hasAttribute('disabled'),
    ).toBe(false)
    expect(screen.getByText('Advertencias')).toBeTruthy()
  })
})
