// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { uploadWithRetries } from '../api/simulatorApi'
import { FRAME_BYTES, readHeader } from '../codec/frame'
import { DEFAULT_SIGNAL_CONFIG } from '../codec/signal'
import type { VestConfig } from '../types'
import { useVestFleet } from './useVestFleet'

vi.mock('../api/simulatorApi', () => ({
  uploadWithRetries: vi.fn(async (body: Uint8Array, headers: { serial: string }) => {
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
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe('useVestFleet', () => {
  it('runAll ejecuta dos chalecos simultáneos con estado independiente', async () => {
    const { result } = renderHook(() => useVestFleet([config('a'), config('b')]))

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

  it('una segunda corrida continúa el reloj y la secuencia del mismo chaleco', async () => {
    const { result } = renderHook(() => useVestFleet([config('a')]))

    await act(async () => result.current.run('a'))
    const first = result.current.vests[0].stats

    await act(async () => result.current.run('a'))
    const second = result.current.vests[0].stats

    expect(second.lastSeq).toBeGreaterThan(first.lastSeq)
    expect(second.uptimeMs).toBeGreaterThan(first.uptimeMs)
    expect(second.studyId).toBe(first.studyId)
  })

  it('el drenado continúa después de un ACK transitoriamente improductivo', async () => {
    vi.mocked(uploadWithRetries).mockResolvedValueOnce({
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
    const { result } = renderHook(() => useVestFleet([config('a')]))

    await act(async () => result.current.run('a'))

    expect(result.current.vests[0].stats.framesPending).toBe(0)
    expect(uploadWithRetries).toHaveBeenCalledTimes(2)
  })
})

function config(id: string): VestConfig {
  return {
    id,
    label: `Chaleco ${id}`,
    deviceId: `device-${id}`,
    serial: `HOL-${id.toUpperCase()}`,
    apiKey: `key-${id}`,
    batchMinutes: 0.001,
    batchCount: 1,
    cadence: { kind: 'instant' },
    placementOk: true,
    signal: { ...DEFAULT_SIGNAL_CONFIG, seed: id.charCodeAt(0) },
    frames: {
      corruptCrcPct: 0,
      duplicatePct: 0,
      dropPct: 0,
      rebootAtBatch: 0,
      simulated: true,
      shuffle: false,
    },
    network: {
      truncateBodyPct: 0,
      invalidApiKey: false,
      unknownSerial: false,
      omitUptime: false,
      maxRetries: 0,
    },
  }
}
