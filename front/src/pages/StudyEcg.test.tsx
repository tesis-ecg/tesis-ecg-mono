// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { EcgSessionRoute } from '@/features/ecg/components/EcgSessionRoute'

const hooks = vi.hoisted(() => ({ study: vi.fn(), ecg: vi.fn() }))
vi.mock('@/features/studies/hooks/useStudyFindings', () => ({
  useStudyFindings: () => ({ data: undefined }),
}))
vi.mock('@/features/studies/hooks/useStudy', () => ({ useStudy: hooks.study }))
vi.mock('@/features/ecg/hooks/useEcgSignal', () => ({ useEcgSignal: hooks.ecg }))
vi.mock('@/features/ecg/components/ECGExpandedView', () => ({
  ECGExpandedView: ({ followLatest, onBack }: { followLatest: boolean; onBack: () => void }) => (
    <div>
      <output data-testid="live">{String(followLatest)}</output>
      <button onClick={onBack}>Volver al estudio</button>
    </div>
  ),
}))

import { StudyEcg } from './StudyEcg'

function mount() {
  return render(
    <MemoryRouter initialEntries={['/studies/one/ecg']}>
      <Routes>
        <Route path="studies/:id" element={<EcgSessionRoute />}>
          <Route index element={<p>Detalle del estudio</p>} />
          <Route path="ecg" element={<StudyEcg />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  hooks.study.mockReturnValue({
    data: { durationMs: 60_000, status: 'in_progress' },
    isLoading: false,
    isError: false,
  })
  hooks.ecg.mockReturnValue({ data: { metadata: {} }, isLoading: false, isError: false })
})
afterEach(cleanup)

describe('StudyEcg — direct entry', () => {
  it('mantiene la actualización en vivo y vuelve al detalle sin historia previa', () => {
    mount()
    expect(hooks.ecg).toHaveBeenCalledWith('one', true)
    expect(screen.getByTestId('live').textContent).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Volver al estudio' }))
    expect(screen.getByText('Detalle del estudio')).toBeTruthy()
  })

  it('no solicita señal para estudios programados y muestra el estado vacío', () => {
    hooks.study.mockReturnValue({
      data: { durationMs: 0, status: 'scheduled' },
      isLoading: false,
      isError: false,
    })
    mount()
    expect(hooks.ecg).toHaveBeenCalledWith(undefined, false)
    expect(screen.getByText('Estudio todavía no iniciado')).toBeTruthy()
  })

  it('muestra espera mientras no haya llegado la primera señal', () => {
    hooks.ecg.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    mount()
    expect(screen.getByRole('status').textContent).toContain('Esperando el primer lote del Holter…')
  })

  it('permite reintentar la carga de la señal', () => {
    const refetch = vi.fn()
    hooks.ecg.mockReturnValue({ isError: true, error: new Error('Sin conexión'), refetch })
    mount()
    expect(screen.getByText('No pudimos cargar la señal ECG')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('permite reintentar la carga del estudio', () => {
    const refetch = vi.fn()
    hooks.study.mockReturnValue({ isError: true, error: new Error('Sin conexión'), refetch })
    mount()
    expect(screen.getByText('No pudimos cargar el estudio')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    expect(refetch).toHaveBeenCalledOnce()
    expect(hooks.ecg).toHaveBeenCalledWith(undefined, false)
  })
})
