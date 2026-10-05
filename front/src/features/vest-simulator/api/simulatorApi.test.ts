import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  FIRMWARE_VERSION,
  postDeviceStatus,
  postFrames,
  uploadWithGrace,
  type IngestHeaders,
} from './simulatorApi'

const headers: IngestHeaders = {
  serial: 'HOL-1',
  apiKey: 'k',
  uptimeMs: 1000,
  bridgeEpochMs: 1_757_000_000_000,
  bootId: 4,
  timeSource: 'ntp',
  timeUncertaintyMs: 200,
  firmwareVersion: FIRMWARE_VERSION,
  batteryPct: 90,
  diag: { 'X-Device-Rssi': '-60', 'X-Device-Sqi': '3' },
}

const ACCEPTED = {
  framesReceived: 1,
  framesAccepted: 1,
  framesRejected: 0,
  framesDuplicate: 0,
  lastAcceptedSeq: 0,
  batchId: 'batch',
  studyId: 'study',
  serverTime: '2026-01-01T00:00:00Z',
}

function lastInit(): RequestInit & { headers: Record<string, string> } {
  const calls = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
  return calls[calls.length - 1][1]
}

describe('subida al endpoint de ingesta', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    globalThis.fetch = originalFetch
  })

  it('manda las cabeceras del puente real', async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify(ACCEPTED), { status: 202 }),
    ) as unknown as typeof fetch

    await postFrames(new Uint8Array(256), headers)

    expect(lastInit().headers).toEqual({
      'Content-Type': 'application/octet-stream',
      Authorization: 'Bearer k',
      'X-Device-Serial': 'HOL-1',
      'X-Device-Boot-Id': '4',
      'X-Device-Uptime-Ms': '1000',
      'X-Bridge-Epoch-Ms': '1757000000000',
      'X-Time-Sync-Source': 'ntp',
      'X-Time-Sync-Uncertainty-Ms': '200',
      'X-Firmware-Version': '2.1.0',
      'X-Battery-Pct': '90',
      'X-Device-Rssi': '-60',
      'X-Device-Sqi': '3',
    })
  })

  it('postFrames expone el error de un 4xx sin reintentos implícitos', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ code: 'DEVICE_UNASSIGNED', message: 'no' }), {
          status: 409,
        }),
    ) as unknown as typeof fetch

    const result = await postFrames(new Uint8Array(256), headers)

    expect(result).toMatchObject({ ok: false, status: 409, errorCode: 'DEVICE_UNASSIGNED' })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('lee el error también dentro de `detail`', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: { code: 'DEVICE_TIME_INVALID', message: 'a 9 h' } }),
          { status: 422 },
        ),
    ) as unknown as typeof fetch

    const result = await postFrames(new Uint8Array(256), headers)

    expect(result).toMatchObject({ errorCode: 'DEVICE_TIME_INVALID', errorMessage: 'a 9 h' })
  })

  it('reintenta el mismo POST ante un 5xx mientras dura la gracia', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'caído' }), { status: 503 }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(ACCEPTED), { status: 202 }),
      ) as unknown as typeof fetch
    const onRetry = vi.fn()

    const pending = uploadWithGrace(
      new Uint8Array(256),
      headers,
      60_000,
      new AbortController().signal,
      onRetry,
    )
    await vi.advanceTimersByTimeAsync(1000)

    await expect(pending).resolves.toMatchObject({ ok: true, status: 202 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(3)
    expect(onRetry).toHaveBeenCalledTimes(2)
  })

  it('vencida la gracia devuelve el último error', async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError('sin conexión')
    }) as unknown as typeof fetch

    const pending = uploadWithGrace(
      new Uint8Array(256),
      headers,
      1000,
      new AbortController().signal,
      vi.fn(),
    )
    await vi.advanceTimersByTimeAsync(2000)

    await expect(pending).resolves.toMatchObject({
      ok: false,
      status: 0,
      errorCode: 'NETWORK_ERROR',
    })
    // 250 ms entre intentos durante 1 s de gracia.
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(5)
  })

  it('con gracia cero no reintenta', async () => {
    globalThis.fetch = vi.fn(
      async () => new Response('{}', { status: 500 }),
    ) as unknown as typeof fetch

    const result = await uploadWithGrace(
      new Uint8Array(256),
      headers,
      0,
      new AbortController().signal,
      vi.fn(),
    )

    expect(result.status).toBe(500)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('no reintenta 4xx ni absorbe una cancelación', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'BAD' }), { status: 422 }))
      .mockRejectedValueOnce(new DOMException('cancelado', 'AbortError')) as unknown as typeof fetch

    const controller = new AbortController()
    const invalid = await uploadWithGrace(
      new Uint8Array(256),
      headers,
      60_000,
      controller.signal,
      vi.fn(),
    )
    await expect(
      uploadWithGrace(new Uint8Array(256), headers, 60_000, controller.signal, vi.fn()),
    ).rejects.toMatchObject({ name: 'AbortError' })

    expect(invalid.status).toBe(422)
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
})

describe('canal corto del chaleco', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('manda JSON con la credencial del equipo y sin la cookie del médico', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ notified: true, alertId: 'a-1', serverTime: '2026-01-01T00:00:00Z' }),
          { status: 200 },
        ),
    ) as unknown as typeof fetch

    const ack = await postDeviceStatus('lead_off', headers, 180, { sqi: 1 })

    expect(ack).toMatchObject({ notified: true, alertId: 'a-1' })
    const [url, init] = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/ingest/device-status')
    // Sin `credentials`: el chaleco no tiene sesión y la cookie del portal no
    // puede viajar por accidente a un endpoint exento del chequeo de Origin.
    expect(init.credentials).toBeUndefined()
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.headers.Authorization).toBe('Bearer k')
    expect(init.headers['X-Device-Boot-Id']).toBe('4')
    expect(JSON.parse(init.body)).toEqual({
      event: 'lead_off',
      durationSeconds: 180,
      batteryPct: 90,
      sqi: 1,
    })
  })

  it('el latido `alive` no inventa SQI', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ notified: false, alertId: null, serverTime: 'x' }), {
          status: 200,
        }),
    ) as unknown as typeof fetch

    await postDeviceStatus('alive', { ...headers, batteryPct: null }, 0)

    expect(JSON.parse(lastInit().body as string)).toEqual({ event: 'alive', durationSeconds: 0 })
  })

  it('propaga el mensaje del backend cuando la credencial no sirve', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ code: 'DEVICE_UNAUTHORIZED', message: 'Credencial inválida.' }),
          {
            status: 401,
          },
        ),
    ) as unknown as typeof fetch

    await expect(postDeviceStatus('lead_off', headers, 180)).rejects.toThrow('Credencial inválida.')
  })
})
