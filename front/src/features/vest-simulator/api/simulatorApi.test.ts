import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { postFrames, uploadWithRetries } from './simulatorApi'

describe('subida al endpoint de ingesta', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    globalThis.fetch = originalFetch
  })

  const headers = {
    serial: 'HOL-1',
    apiKey: 'k',
    uptimeMs: 1000,
    firmwareVersion: '1.0.0',
    batteryPct: 90,
  }

  it('postFrames expone el error de un 4xx sin reintentos implícitos', async () => {
    const calls: number[] = []
    globalThis.fetch = vi.fn(async () => {
      calls.push(Date.now())
      return new Response(JSON.stringify({ code: 'DEVICE_UNASSIGNED', message: 'no' }), {
        status: 409,
      })
    }) as unknown as typeof fetch

    const result = await postFrames(new Uint8Array(256), headers)

    expect(result.ok).toBe(false)
    expect(result.status).toBe(409)
    expect(result.errorCode).toBe('DEVICE_UNASSIGNED')
    expect(calls).toHaveLength(1)
  })

  it('reintenta 5xx y termina cuando el backend responde', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'caído' }), { status: 503 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            framesReceived: 1,
            framesAccepted: 1,
            framesRejected: 0,
            framesDuplicate: 0,
            lastAcceptedSeq: 0,
            batchId: 'batch',
            studyId: 'study',
            serverTime: '2026-01-01T00:00:00Z',
          }),
          { status: 202 },
        ),
      ) as unknown as typeof fetch

    const pending = uploadWithRetries(
      new Uint8Array(256),
      headers,
      2,
      new AbortController().signal,
      vi.fn(),
    )
    await vi.advanceTimersByTimeAsync(500)

    await expect(pending).resolves.toMatchObject({ ok: true, status: 202 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('trata una excepción de red como transitoria', async () => {
    globalThis.fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: 'NO' }), { status: 409 }),
      ) as unknown as typeof fetch

    const pending = uploadWithRetries(
      new Uint8Array(256),
      headers,
      1,
      new AbortController().signal,
      vi.fn(),
    )
    await vi.advanceTimersByTimeAsync(500)

    await expect(pending).resolves.toMatchObject({ ok: false, status: 409 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('no reintenta 4xx ni absorbe una cancelación', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'BAD' }), { status: 422 }))
      .mockRejectedValueOnce(new DOMException('cancelado', 'AbortError')) as unknown as typeof fetch

    const controller = new AbortController()
    const invalid = await uploadWithRetries(
      new Uint8Array(256),
      headers,
      3,
      controller.signal,
      vi.fn(),
    )
    await expect(
      uploadWithRetries(new Uint8Array(256), headers, 3, controller.signal, vi.fn()),
    ).rejects.toMatchObject({ name: 'AbortError' })

    expect(invalid.status).toBe(422)
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('devuelve el último error cuando agota los reintentos', async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError('sin conexión')
    }) as unknown as typeof fetch

    const pending = uploadWithRetries(
      new Uint8Array(256),
      headers,
      1,
      new AbortController().signal,
      vi.fn(),
    )
    await vi.advanceTimersByTimeAsync(500)

    await expect(pending).resolves.toMatchObject({
      ok: false,
      status: 0,
      errorCode: 'NETWORK_ERROR',
    })
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
})
