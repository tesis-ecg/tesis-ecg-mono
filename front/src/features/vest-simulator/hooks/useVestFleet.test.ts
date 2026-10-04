// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { postDeviceStatus, uploadWithGrace, type IngestHeaders } from '../api/simulatorApi'
import { FRAME_BYTES, readHeader } from '../codec/frame'
import { DEFAULT_SIGNAL_PROFILE } from '../codec/signal'
import { makeEpisode } from '../defaults'
import { memoryFlashStore } from '../flashStore'
import type { VestConfig } from '../types'
import { useVestFleet } from './useVestFleet'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...original,
    uploadWithGrace: vi.fn(async (body: Uint8Array, headers: { serial: string }) => {
      const frameCount = body.length / FRAME_BYTES
      const last = readHeader(body.subarray(body.length - FRAME_BYTES))
      return {
        ok: true,
        status: 202,
        ack: {
          framesReceived: frameCount,
          framesAccepted: frameCount,
          framesRejected: 0,
          framesDuplicate: 0,
          lastAcceptedSeq: last.seq,
          batchId: `batch-${headers.serial}-${last.seq}`,
          studyId: `study-${headers.serial}`,
          serverTime: '2026-01-01T00:00:00Z',
        },
        errorCode: null,
        errorMessage: null,
      }
    }),
    postDeviceStatus: vi.fn(async () => ({
      notified: true,
      alertId: 'alert-1',
      serverTime: '2026-01-01T00:00:00Z',
    })),
  }
})

const NOW = new Date(2026, 9, 3, 15).getTime()

// El `localStorage` de este entorno no siempre existe: uno en memoria, para que
// el reloj persista entre dos montajes del hook como en el navegador.
beforeEach(() => {
  const store = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
    },
  })
})

afterEach(() => {
  vi.clearAllMocks()
})

/** Las tramas y cabeceras de cada POST que salió. */
function posts() {
  return vi.mocked(uploadWithGrace).mock.calls.map(([body, headers]) => ({
    seqs: Array.from({ length: body.length / FRAME_BYTES }, (_, i) =>
      readHeader(body.subarray(i * FRAME_BYTES, (i + 1) * FRAME_BYTES)),
    ),
    headers: headers as IngestHeaders,
  }))
}

describe('useVestFleet', () => {
  it('runAll ejecuta dos chalecos simultáneos con estado independiente', async () => {
    const { result } = renderHook(() =>
      useVestFleet([config('a'), config('b')], { flashStore: memoryFlashStore(), now: () => NOW }),
    )

    act(() => result.current.runAll())

    await waitFor(() => {
      expect(result.current.vests.map((vest) => vest.phase)).toEqual(['done', 'done'])
    })
    expect(result.current.vests.map((vest) => vest.stats.studyId)).toEqual([
      'study-HOL-A',
      'study-HOL-B',
    ])
    expect(result.current.vests.every((vest) => vest.stats.framesAccepted > 0)).toBe(true)
  })

  it('manda POSTs de 48 tramas como el puente', async () => {
    const { result } = renderHook(() =>
      useVestFleet([config('a', { batchMinutes: 1 })], {
        flashStore: memoryFlashStore(),
        now: () => NOW,
      }),
    )

    await act(async () => result.current.run('a'))

    const sent = posts()
    expect(sent.length).toBeGreaterThan(1)
    expect(sent.every((post) => post.seqs.length <= 48)).toBe(true)
    expect(result.current.vests[0].stats.framesPending).toBe(0)
  })

  it('la primera corrida termina en la hora actual', async () => {
    const { result } = renderHook(() =>
      useVestFleet([config('a', { batchMinutes: 1, batchCount: 2 })], {
        flashStore: memoryFlashStore(),
        now: () => NOW,
      }),
    )

    await act(async () => result.current.run('a'))

    const sent = posts()
    const first = sent[0]
    const bootEpoch = first.headers.bridgeEpochMs - first.headers.uptimeMs!
    // Lo que calcula el backend para la primera muestra.
    expect(bootEpoch + first.seqs[0].t0Ms).toBe(NOW - 2 * 60_000)
    expect(sent.every((post) => post.headers.bridgeEpochMs === NOW)).toBe(true)
    expect(result.current.vests[0].stats.dataCursorEpochMs).toBe(NOW)
  })

  it('después de horas sin usarlo manda la hora real y la señal sigue donde quedó', async () => {
    // El caso del `422 X-Bridge-Epoch-Ms está a 43826 s de la hora del servidor`.
    let now = NOW
    const { result } = renderHook(() =>
      useVestFleet([config('a', { batchMinutes: 1 })], {
        flashStore: memoryFlashStore(),
        now: () => now,
      }),
    )

    await act(async () => result.current.run('a'))
    const firstRun = posts()
    const lastSeq = firstRun[firstRun.length - 1].seqs.at(-1)!
    vi.mocked(uploadWithGrace).mockClear()

    now = NOW + 12 * 3_600_000
    await act(async () => result.current.run('a'))

    const second = posts()
    const headers = second[0].headers
    expect(headers.bridgeEpochMs).toBe(now)
    const bootEpoch = headers.bridgeEpochMs - headers.uptimeMs!
    // Arranca justo donde terminó el envío anterior, no en la hora actual.
    expect(second[0].seqs[0].seq).toBe(lastSeq.seq + 1)
    expect(bootEpoch + second[0].seqs[0].t0Ms).toBe(NOW)
    expect(result.current.vests[0].stats.studyId).toBe('study-HOL-A')
  })

  it('el drenado continúa después de un ACK transitoriamente improductivo', async () => {
    vi.mocked(uploadWithGrace).mockResolvedValueOnce({
      ok: true,
      status: 202,
      ack: {
        framesReceived: 1,
        framesAccepted: 0,
        framesRejected: 0,
        framesDuplicate: 0,
        lastAcceptedSeq: null,
        batchId: null,
        studyId: 'study-HOL-A',
        serverTime: '2026-01-01T00:00:00Z',
      },
      errorCode: null,
      errorMessage: null,
    })
    const { result } = renderHook(() =>
      useVestFleet([config('a')], { flashStore: memoryFlashStore(), now: () => NOW }),
    )

    await act(async () => result.current.run('a'))

    expect(result.current.vests[0].stats.framesPending).toBe(0)
  })

  it('la flash sobrevive a recargar la página', async () => {
    const store = memoryFlashStore()
    vi.mocked(uploadWithGrace).mockResolvedValue({
      ok: false,
      status: 503,
      ack: null,
      errorCode: null,
      errorMessage: 'caído',
    })
    const first = renderHook(() =>
      useVestFleet([config('a')], { flashStore: store, now: () => NOW }),
    )
    await act(async () => first.result.current.run('a'))
    const pending = first.result.current.vests[0].stats.framesPending
    expect(pending).toBeGreaterThan(0)
    first.unmount()

    vi.mocked(uploadWithGrace).mockReset()
    vi.mocked(uploadWithGrace).mockImplementation(async (body) => {
      const last = readHeader(body.subarray(body.length - FRAME_BYTES))
      const count = body.length / FRAME_BYTES
      return {
        ok: true,
        status: 202,
        ack: {
          framesReceived: count,
          framesAccepted: count,
          framesRejected: 0,
          framesDuplicate: 0,
          lastAcceptedSeq: last.seq,
          batchId: 'b',
          studyId: 'study-HOL-A',
          serverTime: 'x',
        },
        errorCode: null,
        errorMessage: null,
      }
    })
    const second = renderHook(() =>
      useVestFleet([config('a')], { flashStore: store, now: () => NOW + 60_000 }),
    )
    await act(async () => second.result.current.run('a'))

    // Lo que no se había confirmado sale primero, desde la seq 0.
    expect(posts()[0].seqs[0].seq).toBe(0)
    expect(second.result.current.vests[0].stats.framesPending).toBe(0)
  })

  it('el equipo avisa solo cuando el electrodo queda suelto', async () => {
    const { result } = renderHook(() =>
      useVestFleet(
        [
          config('a', {
            batchMinutes: 4,
            episodes: [makeEpisode('lead_off', { startSec: 30, durationSec: 150 })],
          }),
        ],
        { flashStore: memoryFlashStore(), now: () => NOW + 3_600_000 },
      ),
    )

    await act(async () => result.current.run('a'))

    const events = vi.mocked(postDeviceStatus).mock.calls.map(([event]) => event)
    expect(events).toContain('lead_off')
    expect(result.current.vests[0].config.placementOk).toBe(false)
    const flags = posts()
      .map((post) => post.headers.diag['X-Device-Lead-Flags'])
      .filter(Boolean)
    expect(Number(flags[0]) & 0x20).toBeTruthy()
  })

  it('una arritmia inyectada se consume en el próximo lote', async () => {
    const { result } = renderHook(() =>
      useVestFleet([config('a', { batchMinutes: 1 })], {
        flashStore: memoryFlashStore(),
        now: () => NOW,
      }),
    )

    act(() => result.current.injectAnomaly('a', 'afib'))
    expect(result.current.vests[0].config.pendingInjections).toHaveLength(1)

    await act(async () => result.current.run('a'))

    expect(result.current.vests[0].config.pendingInjections).toHaveLength(0)
    expect(result.current.vests[0].log.some((entry) => entry.message.includes('Fibrilación'))).toBe(
      true,
    )
  })

  it('el reinicio conserva el backlog y lo manda con la hora de su arranque', async () => {
    vi.mocked(uploadWithGrace).mockResolvedValueOnce({
      ok: false,
      status: 503,
      ack: null,
      errorCode: null,
      errorMessage: 'caído',
    })
    const { result } = renderHook(() =>
      useVestFleet(
        [config('a', { batchCount: 2, frames: { ...config('a').frames, rebootAtBatch: 2 } })],
        { flashStore: memoryFlashStore(), now: () => NOW },
      ),
    )

    await act(async () => result.current.run('a'))

    const sent = posts().slice(1)
    const byBoot = new Map<number, IngestHeaders>()
    for (const post of sent) {
      const boots = new Set(post.seqs.map((h) => h.bootId))
      // Un POST nunca mezcla arranques, y declara el suyo.
      expect(boots.size).toBe(1)
      expect(post.headers.bootId).toBe(post.seqs[0].bootId)
      byBoot.set(post.headers.bootId, post.headers)
    }
    expect([...byBoot.keys()].sort()).toEqual([0, 1])
    const anchor = (h: IngestHeaders) => h.bridgeEpochMs - h.uptimeMs!
    expect(anchor(byBoot.get(1)!)).toBeGreaterThan(anchor(byBoot.get(0)!))
    expect(result.current.vests[0].stats.framesPending).toBe(0)
  })
})

function config(id: string, overrides: Partial<VestConfig> = {}): VestConfig {
  return {
    id,
    label: `Chaleco ${id}`,
    deviceId: `device-${id}`,
    serial: `HOL-${id.toUpperCase()}`,
    apiKey: `key-${id}`,
    batchMinutes: 0.2,
    batchCount: 1,
    cadence: { kind: 'instant' },
    placementOk: true,
    signal: { ...DEFAULT_SIGNAL_PROFILE, seed: id.charCodeAt(0) },
    episodes: [],
    pendingInjections: [],
    frames: {
      corruptCrcPct: 0,
      duplicatePct: 0,
      dropPct: 0,
      rebootAtBatch: 0,
      simulated: true,
      shuffle: false,
    },
    network: {
      postFrames: 48,
      graceSeconds: 0,
      rssiDbm: -60,
      truncateBodyPct: 0,
      invalidApiKey: false,
      unknownSerial: false,
      omitUptime: false,
      noSntp: false,
      lostBootTable: false,
    },
    ...overrides,
  }
}
