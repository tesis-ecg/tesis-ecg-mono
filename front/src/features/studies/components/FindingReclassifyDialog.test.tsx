// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HolterMetrics, Study, StudyFindings } from '@/features/studies/types'
import { api } from '@/lib/api'

HTMLElement.prototype.scrollIntoView = vi.fn()

const hooks = vi.hoisted(() => ({ findings: vi.fn(), metrics: vi.fn() }))
vi.mock('@/features/studies/hooks/useStudyFindings', () => ({ useStudyFindings: hooks.findings }))
vi.mock('@/features/studies/hooks/useHolterMetrics', () => ({ useHolterMetrics: hooks.metrics }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { StudyAnalysisTab } from './StudyAnalysisTab'

const T0 = Date.UTC(2026, 8, 16, 12, 0, 0)

const study = {
  id: 'study-1',
  status: 'completed',
} as Study

const baseFinding = {
  category: 'clinical' as const,
  severity: 'medium' as const,
  startOffsetMs: 60_000,
  endOffsetMs: 70_000,
  startEpochMs: T0 + 60_000,
  endEpochMs: T0 + 70_000,
  confidenceScore: null,
  modelVersion: 'ml-1',
  reclassifiable: true,
}

function findings(): StudyFindings {
  return {
    studyId: 'study-1',
    sampleRate: 500,
    sampleCount: 1,
    durationMs: 1,
    modelVersion: 'ml-1',
    quality: { analyzableRatio: 1, goodRatio: 1, marginalRatio: 0, badRatio: 0, evaluatedMs: 1 },
    groups: [],
    ungrouped: [
      { ...baseFinding, id: 'f-v', kind: 'ventricular_ectopy', validationStatus: 'pending' },
      {
        ...baseFinding,
        id: 'f-corrected',
        kind: 'svt_run',
        startEpochMs: T0 + 120_000,
        endEpochMs: T0 + 130_000,
        validationStatus: 'reclassified',
        correction: {
          action: 'relabel',
          beatLabel: 'V',
          effectiveKind: 'vt_run',
          note: 'Se ve ancho',
          correctedBy: 'u-1',
          correctedByName: 'Dra. Gómez',
          correctedAt: '2026-09-17T12:00:00Z',
        },
      },
      {
        ...baseFinding,
        id: 'f-manual',
        kind: 'pause',
        modelVersion: null,
        reclassifiable: false,
        validationStatus: 'pending',
      },
    ],
    totals: {},
    truncated: false,
  } as unknown as StudyFindings
}

function metrics(): HolterMetrics {
  return {
    status: 'ok',
    unavailableReason: null,
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
  } as unknown as HolterMetrics
}

let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  hooks.findings.mockReturnValue({ data: findings(), isLoading: false })
  hooks.metrics.mockReturnValue({ data: metrics(), isLoading: false })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function renderTab() {
  render(
    <QueryClientProvider client={client}>
      <StudyAnalysisTab study={study} onLocate={vi.fn()} />
    </QueryClientProvider>,
  )
}

describe('reclasificación de hallazgos', () => {
  it('muestra el estado de corrección: original, quién y cuándo', () => {
    renderTab()

    const status = document.querySelector(
      '[data-finding-id="f-corrected"] [data-correction-status]',
    )!
    expect(status.textContent).toContain('Reclasificado')
    expect(status.textContent).toContain('Dra. Gómez')
    expect(status.textContent).toContain(
      'Original: Posible taquicardia paroxística supraventricular',
    )
    expect(status.textContent).toContain('Nota: Se ve ancho')
  })

  it('no ofrece reclasificar lo que no produjo el motor', () => {
    renderTab()

    expect(screen.getAllByRole('button', { name: /^Reclasificar / })).toHaveLength(2)
  })

  it('envía la reclasificación con nota y refresca las métricas con la respuesta', async () => {
    const recalculated = { ...metrics(), unavailableReason: 'recalculadas' }
    const post = vi.spyOn(api, 'post').mockResolvedValue({
      data: { finding: findings().ungrouped![0], holterMetrics: recalculated },
    })
    const setData = vi.spyOn(client, 'setQueryData')
    renderTab()

    fireEvent.click(
      screen.getByRole('button', { name: /^Reclasificar Extrasístoles ventriculares/ }),
    )
    const dialog = screen.getByRole('dialog')
    const save = within(dialog).getByRole('button', { name: 'Guardar corrección' })
    expect((save as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(within(dialog).getByRole('radio', { name: /Marcar como otro hallazgo/ }))
    fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Tipo de hallazgo' }), {
      key: 'ArrowDown',
    })
    fireEvent.click(screen.getByRole('option', { name: 'Extrasístoles supraventriculares (S)' }))
    fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Gravedad' }), {
      key: 'ArrowDown',
    })
    fireEvent.click(screen.getByRole('option', { name: /^Alta$/ }))
    fireEvent.change(within(dialog).getByLabelText('Nota (opcional)'), {
      target: { value: 'Es prematuro y angosto' },
    })
    fireEvent.click(save)

    await waitFor(() => expect(post).toHaveBeenCalledOnce())
    expect(post).toHaveBeenCalledWith('/studies/study-1/findings/f-v/reclassification', {
      action: 'relabel',
      kind: 'supraventricular_ectopy',
      severity: 'high',
      note: 'Es prematuro y angosto',
    })
    await waitFor(() =>
      expect(setData).toHaveBeenCalledWith(['studies', 'study-1', 'holter-metrics'], recalculated),
    )
  })

  it('ofrece las mismas dos acciones en un hallazgo ya corregido', () => {
    renderTab()

    fireEvent.click(screen.getByRole('button', { name: /^Reclasificar Posible taquicardia/ }))

    expect(screen.queryByLabelText(/Confirmar hallazgo/)).toBeNull()
    expect(screen.getAllByRole('radio')).toHaveLength(2)
    expect(screen.getByRole('radio', { name: /Marcar como otro hallazgo/ })).toBeTruthy()
  })
})

it('exige elegir el hallazgo y descarta sin enviar los selectores de otra acción', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({
    data: { finding: findings().ungrouped![0], holterMetrics: metrics() },
  })
  renderTab()
  fireEvent.click(screen.getByRole('button', { name: /^Reclasificar Extrasístoles ventriculares/ }))
  const dialog = screen.getByRole('dialog')
  const view = within(dialog)
  expect(view.queryByRole('combobox')).toBeNull()
  expect(view.queryByRole('radio', { name: /Confirmar/ })).toBeNull()
  fireEvent.click(view.getByRole('radio', { name: /Marcar como otro hallazgo/ }))
  expect(view.getAllByRole('combobox')).toHaveLength(2)
  const save = view.getByRole('button', { name: 'Guardar corrección' }) as HTMLButtonElement
  expect(save.disabled).toBe(true)
  fireEvent.keyDown(view.getByRole('combobox', { name: 'Tipo de hallazgo' }), { key: 'ArrowDown' })
  fireEvent.click(screen.getByRole('option', { name: 'Posible bloqueo AV completo' }))
  expect(save.disabled).toBe(false)
  fireEvent.keyDown(view.getByRole('combobox', { name: 'Gravedad' }), { key: 'ArrowDown' })
  expect(screen.getAllByRole('option')).toHaveLength(4)
  fireEvent.click(screen.getByRole('option', { name: /^Crítica$/ }))
  fireEvent.click(view.getByRole('radio', { name: /^Descartar/ }))
  expect(view.queryByRole('combobox')).toBeNull()
  fireEvent.click(save)
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith('/studies/study-1/findings/f-v/reclassification', {
      action: 'reject',
      note: null,
    }),
  )
})
