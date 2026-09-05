// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HolterApiKeyCard } from './HolterApiKeyCard'
import type { Holter } from '../types'

const getHolterApiKey = vi.fn()
const writeText = vi.fn()

vi.mock('../api/devicesApi', () => ({
  getHolterApiKey: (id: string) => getHolterApiKey(id),
  rotateHolterApiKey: vi.fn(),
}))

/**
 * Las dos cosas que hacen que esta card sea segura de tener siempre en pantalla:
 * la key arranca oculta, y no se le pide al servidor hasta que alguien la pide.
 * Lo segundo importa porque el backend audita cada lectura — traerla al montar
 * llenaría la auditoría de eventos que nadie provocó.
 */
describe('HolterApiKeyCard', () => {
  // Sin `globals: true` ni setup file, RTL no limpia el DOM sola entre tests.
  afterEach(cleanup)

  beforeEach(() => {
    getHolterApiKey.mockReset()
    writeText.mockReset()
    writeText.mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    getHolterApiKey.mockResolvedValue({
      deviceId: 'dev-1',
      serial: 'HOL-001',
      apiKey: 'k3y-en-claro-de-prueba',
      rotatedAt: '2026-09-04T12:00:00Z',
    })
  })

  it('arranca oculta y no le pide la key al servidor', () => {
    renderCard()

    expect(screen.queryByText('k3y-en-claro-de-prueba')).toBeNull()
    expect(getHolterApiKey).not.toHaveBeenCalled()
    expect(
      (screen.getByRole('button', { name: 'Copiar API key' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('la trae y la muestra recién cuando se toca el ojo', async () => {
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: 'Mostrar' }))

    await waitFor(() => expect(screen.getByText('k3y-en-claro-de-prueba')).toBeTruthy())
    expect(getHolterApiKey).toHaveBeenCalledWith('dev-1')
    expect(
      (screen.getByRole('button', { name: 'Copiar API key' }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('vuelve a ocultarla sin perderla', async () => {
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: 'Mostrar' }))
    await waitFor(() => expect(screen.getByText('k3y-en-claro-de-prueba')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Ocultar' }))

    expect(screen.queryByText('k3y-en-claro-de-prueba')).toBeNull()
  })

  it('copia la key en el primer click una vez revelada', async () => {
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: 'Mostrar' }))
    await waitFor(() => expect(screen.getByText('k3y-en-claro-de-prueba')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Copiar API key' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('k3y-en-claro-de-prueba'))
  })

  it('la muestra sola para un equipo recién creado', async () => {
    renderCard({ defaultRevealed: true })

    await waitFor(() => expect(screen.getByText('k3y-en-claro-de-prueba')).toBeTruthy())
  })
})

function renderCard(props: { defaultRevealed?: boolean } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <HolterApiKeyCard holter={holter()} {...props} />
    </QueryClientProvider>,
  )
}

function holter(): Holter {
  return {
    id: 'dev-1',
    serial: 'HOL-001',
    model: 'Holter ECG',
    firmwareVersion: '1.0.0',
    status: 'available',
    assignedPatientId: null,
    assignedPatientName: null,
    activeStudyId: null,
    lastSeenAt: null,
    createdAt: '2026-09-01T12:00:00Z',
  }
}
