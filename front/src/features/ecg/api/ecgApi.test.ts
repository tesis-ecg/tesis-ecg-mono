import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'

import { getStudyEcg } from './ecgApi'

describe('getStudyEcg', () => {
  const originalFetch = globalThis.fetch
  const originalWorker = globalThis.Worker

  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.fetch = originalFetch
    globalThis.Worker = originalWorker
  })

  it('no consulta el endpoint legacy cuando el estudio no tiene ECG', async () => {
    const error = createApiError({
      status: 404,
      code: 'NOT_FOUND',
      serverCode: 'ECG_NOT_FOUND',
      message: 'ECG no disponible para este estudio.',
    })
    const getSpy = vi.spyOn(api, 'get').mockRejectedValue(error)

    await expect(getStudyEcg('study-id')).rejects.toBe(error)
    expect(getSpy).toHaveBeenCalledOnce()
    expect(getSpy).toHaveBeenCalledWith('/studies/study-id/ecg/manifest', {
      signal: undefined,
    })
  })

  it('usa el endpoint legacy si el backend no reconoce la ruta de manifest', async () => {
    const routeNotFound = createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Recurso no encontrado.',
    })
    const legacyFailure = new Error('legacy request reached')
    const getSpy = vi
      .spyOn(api, 'get')
      .mockRejectedValueOnce(routeNotFound)
      .mockRejectedValueOnce(legacyFailure)

    await expect(getStudyEcg('study-id')).rejects.toBe(legacyFailure)
    expect(getSpy).toHaveBeenCalledTimes(2)
    expect(getSpy).toHaveBeenNthCalledWith(2, '/studies/study-id/ecg', {
      signal: undefined,
    })
  })

  it('comprime huecos de pared usando la duración derivada de las muestras', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1000,
        sampleRate: 500,
        durationMs: 3 * 60 * 60 * 1000,
        levels: [object({ samplesPerBucket: 256, pointCount: 4 })],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.sampleRate).toBe(500)
    expect(signal.durationMs).toBe(2000)
  })

  it('concatena en orden los segmentos de un estudio demasiado corto para la pirámide', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 3,
        levels: [],
        segments: [
          object({ url: 'second', startSampleIndex: 2, sampleCount: 1 }),
          object({ url: 'first', startSampleIndex: 0, sampleCount: 2 }),
        ],
      }),
    })
    globalThis.fetch = vi.fn(async (input) =>
      floatResponse(String(input) === 'first' ? [1, 2] : [3]),
    ) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect([...signal.samples]).toEqual([1, 2, 3])
    expect(signal.durationMs).toBe(6)
  })
})

function object(overrides: Record<string, unknown> = {}) {
  return {
    url: 'level',
    expiresAt: '2026-01-01T00:00:00Z',
    byteLength: 16,
    sha256: null,
    ...overrides,
  }
}

function manifest(overrides: Record<string, unknown> = {}) {
  return {
    formatVersion: 2,
    encoding: 'float32-le',
    sampleRate: 500,
    sampleCount: 1000,
    startTimestamp: 1_700_000_000_000,
    durationMs: 2000,
    raw: null,
    levels: [],
    segments: [],
    ...overrides,
  }
}

function floatResponse(values: number[]): Response {
  return new Response(new Float32Array(values).buffer, { status: 200 })
}

function installDecoderWorker() {
  class DecoderWorker {
    onerror: (() => void) | null = null
    onmessage: ((event: MessageEvent) => void) | null = null

    postMessage(message: { buffer: ArrayBuffer }) {
      this.onmessage?.({ data: { ok: true, samples: message.buffer } } as MessageEvent)
    }

    terminate() {}
  }
  globalThis.Worker = DecoderWorker as unknown as typeof Worker
}
