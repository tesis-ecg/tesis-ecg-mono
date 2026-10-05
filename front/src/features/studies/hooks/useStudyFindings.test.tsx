// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'

import { useStudyFindings } from './useStudyFindings'

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

describe('useStudyFindings', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('pide los hallazgos del motor del estudio', async () => {
    const payload = { studyId: 'study-1' }
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue({ data: payload })

    const { result } = renderHook(() => useStudyFindings('study-1'), { wrapper: wrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getSpy).toHaveBeenCalledWith('/studies/study-1/findings')
    expect(result.current.data).toBe(payload)
  })

  it('no pide nada sin estudio', () => {
    const getSpy = vi.spyOn(api, 'get')

    renderHook(() => useStudyFindings(undefined), { wrapper: wrapper() })

    expect(getSpy).not.toHaveBeenCalled()
  })

  it('se refresca cada minuto mientras el estudio está en curso: el motor analiza cada lote que llega', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue({ data: {} })

    const { result } = renderHook(() => useStudyFindings('study-1', true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await vi.advanceTimersByTimeAsync(60_000)

    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(2))
  })

  it('no se refresca solo en un estudio cerrado', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue({ data: {} })

    const { result } = renderHook(() => useStudyFindings('study-1', false), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await vi.advanceTimersByTimeAsync(120_000)

    expect(getSpy).toHaveBeenCalledOnce()
  })
})
