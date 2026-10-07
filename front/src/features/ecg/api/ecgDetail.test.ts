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

/** El reloj del navegador en los tests: se adelanta a mano. */
let clock = NOW

function source(
  pieces: DetailPiece[],
  refresh = vi.fn(async () => pieces),
  receivedAt: number | undefined = NOW - 5 * 60_000,
) {
  return createEcgDetailSource({
    studyId: 'study',
    pieces,
    download,
    refresh,
    timestamps: (first, count) => ({
      timestampsMs: Float64Array.from({ length: count }, (_, i) => (first + i) * 2),
      gapIndices: [],
    }),
    receivedAt,
    now: () => clock,
  })
}

/** Una URL firmada con SigV4 que vale `seconds` desde que se firmó. */
function signedUrl(start: number, seconds = 600, version = 1): string {
  return `https://s3/segment-${start}?X-Amz-Date=20261006T115000Z&X-Amz-Expires=${seconds}&v=${version}`
}

const forbidden = () => createApiError({ status: 403, code: 'UNKNOWN', message: 'prohibido' })

afterEach(() => {
  clearDetailCache()
  download.mockReset()
  download.mockImplementation(async (p: DetailPiece) =>
    Float32Array.from({ length: p.sampleCount }, (_, i) => p.startSampleIndex + i),
  )
  clock = NOW
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

  it('con el reloj adelantado no da por vencida una URL recién firmada', async () => {
    // La PC va 11 min adelantada: el `expiresAt` del servidor ya "pasó", pero
    // la URL llegó recién y vale 10 min.
    const fresh = piece(0, 100, {
      url: signedUrl(0),
      expiresAt: new Date(NOW - 60_000).toISOString(),
    })
    const refresh = vi.fn(async () => [fresh])
    const detail = source([fresh], refresh, NOW)

    await detail.load(0, 100)

    expect(refresh).not.toHaveBeenCalled()
    expect(download).toHaveBeenCalledOnce()
  })

  it('una URL firmada vence a los X-Amz-Expires de haber llegado', async () => {
    // Con el reloj atrasado el `expiresAt` parece lejano, pero la URL llegó
    // hace 9 min 50 s y vale 10.
    const stale = piece(0, 100, {
      url: signedUrl(0),
      expiresAt: new Date(NOW + 60 * 60_000).toISOString(),
    })
    const fresh = piece(0, 100, { url: signedUrl(0, 600, 2) })
    const refresh = vi.fn(async () => [fresh])
    const detail = source([stale], refresh, NOW - 590_000)

    await detail.load(0, 100)

    expect(refresh).toHaveBeenCalledOnce()
    expect(download.mock.calls[0][0].url).toBe(signedUrl(0, 600, 2))
  })

  it('un 403 con el manifest recién pedido no se arregla renovando', async () => {
    // Las URLs llegaron hace un momento: el 403 es otra cosa (un objeto que
    // falta) y pedir el manifest en cada zoom solo le gasta CPU a la API.
    const refresh = vi.fn(async () => [piece(0, 100)])
    download.mockRejectedValue(forbidden())
    const detail = source([piece(0, 100)], refresh, NOW - 5_000)

    await expect(detail.load(0, 100)).rejects.toMatchObject({ status: 403 })
    clock += 10_000
    await expect(detail.load(0, 100)).rejects.toMatchObject({ status: 403 })

    expect(refresh).not.toHaveBeenCalled()
    expect(download).toHaveBeenCalledTimes(2)
  })

  it('entre dos renovaciones pasa al menos un minuto', async () => {
    const refresh = vi.fn(async () => [piece(0, 100)])
    download.mockRejectedValue(forbidden())
    const detail = source([piece(0, 100)], refresh)

    // El primer 403 renueva; los que siguen enseguida, no.
    await expect(detail.load(0, 100)).rejects.toMatchObject({ status: 403 })
    clock += 30_000
    await expect(detail.load(0, 100)).rejects.toMatchObject({ status: 403 })
    expect(refresh).toHaveBeenCalledOnce()

    // Pasado el minuto, un 403 vuelve a valer un intento.
    clock += 31_000
    await expect(detail.load(0, 100)).rejects.toMatchObject({ status: 403 })
    expect(refresh).toHaveBeenCalledTimes(2)
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
