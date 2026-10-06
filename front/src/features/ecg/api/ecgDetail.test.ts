import { afterEach, describe, expect, it, vi } from 'vitest'

import { createApiError } from '@/lib/apiError'

import { clearDetailCache, createEcgDetailSource, type DetailPiece } from './ecgDetail'

const NOW = Date.parse('2026-10-06T12:00:00Z')

function piece(start: number, count: number, overrides: Partial<DetailPiece> = {}): DetailPiece {
  return {
    url: `https://s3/segment-${start}?v=1`,
    expiresAt: new Date(NOW + 10 * 60_000).toISOString(),
    byteLength: count * 4,
    sha256: `sha-${start}`,
    startSampleIndex: start,
    sampleCount: count,
    ...overrides,
  }
}

/** Cada muestra vale su índice en el estudio: se ve enseguida si un corte se corrió. */
const download = vi.fn(async (p: DetailPiece) =>
  Float32Array.from({ length: p.sampleCount }, (_, i) => p.startSampleIndex + i),
)

function source(pieces: DetailPiece[], refresh = vi.fn(async () => pieces)) {
  return createEcgDetailSource({
    studyId: 'study',
    pieces,
    download,
    refresh,
    timestamps: (first, count) => ({
      timestampsMs: Float64Array.from({ length: count }, (_, i) => (first + i) * 2),
      gapIndices: [],
    }),
    now: () => NOW,
  })
}

afterEach(() => {
  clearDetailCache()
  download.mockClear()
})

describe('createEcgDetailSource', () => {
  it('arma el tramo pedido cortando de los segmentos que lo tocan', async () => {
    const detail = source([piece(0, 100), piece(100, 100), piece(200, 100)])

    const window = await detail.load(150, 250)

    expect(detail.sampleCount).toBe(300)
    expect(window.startSample).toBe(150)
    expect(window.endSample).toBe(250)
    expect(window.samples[0]).toBe(150)
    expect(window.samples[99]).toBe(249)
    expect(window.timestampsMs[0]).toBe(300)
    // El primer segmento no toca el tramo: no se descarga.
    expect(download.mock.calls.map(([p]) => p.startSampleIndex)).toEqual([100, 200])
  })

  it('recorta a lo que hay', async () => {
    const detail = source([piece(0, 100)])

    const window = await detail.load(-50, 500)

    expect(window.startSample).toBe(0)
    expect(window.endSample).toBe(100)
    expect(window.samples).toHaveLength(100)
  })

  it('un segmento ya descargado no se vuelve a bajar', async () => {
    const detail = source([piece(0, 100), piece(100, 100)])

    await detail.load(0, 150)
    await detail.load(50, 200)

    expect(download).toHaveBeenCalledTimes(2)
  })

  it('renueva las URLs que están por vencer antes de usarlas', async () => {
    const stale = piece(0, 100, { expiresAt: new Date(NOW + 5_000).toISOString() })
    const fresh = piece(0, 100, { url: 'https://s3/segment-0?v=2' })
    const refresh = vi.fn(async () => [fresh])
    const detail = source([stale], refresh)

    await detail.load(0, 100)

    expect(refresh).toHaveBeenCalledOnce()
    expect(download.mock.calls[0][0].url).toBe('https://s3/segment-0?v=2')
  })

  it('ante un 403 renueva una vez y reintenta', async () => {
    const fresh = piece(0, 100, { url: 'https://s3/segment-0?v=2' })
    const refresh = vi.fn(async () => [fresh])
    download.mockRejectedValueOnce(
      createApiError({ status: 403, code: 'UNKNOWN', message: 'vencida' }),
    )
    const detail = source([piece(0, 100)], refresh)

    const window = await detail.load(0, 100)

    expect(refresh).toHaveBeenCalledOnce()
    expect(download.mock.calls.map(([p]) => p.url)).toEqual([
      'https://s3/segment-0?v=1',
      'https://s3/segment-0?v=2',
    ])
    expect(window.samples).toHaveLength(100)
  })

  it('otro error no se disfraza de URL vencida', async () => {
    const refresh = vi.fn(async () => [])
    download.mockRejectedValueOnce(new Error('checksum'))
    const detail = source([piece(0, 100)], refresh)

    await expect(detail.load(0, 100)).rejects.toThrow('checksum')
    expect(refresh).not.toHaveBeenCalled()
  })

  it('corta el tramo en un segmento que falta en vez de pegar los dos lados', async () => {
    const detail = source([piece(0, 100), piece(150, 100)])

    const window = await detail.load(50, 200)

    expect(window.endSample).toBe(100)
    expect(window.samples).toHaveLength(50)
  })
})
