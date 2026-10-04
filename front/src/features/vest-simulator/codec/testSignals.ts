/** Señales deterministas para los tests del codec. */

import type { VestWorkerRequest } from './batchBuilder'
import { STEP_MS } from './frame'
import type { EcgSample } from './riceEncoder'
import { DEFAULT_SIGNAL_PROFILE, initialGeneratorState } from './signal'

export function flatSamples(count: number, startMs = 0): EcgSample[] {
  return Array.from({ length: count }, (_, i) => ({
    timestampMs: startMs + i * STEP_MS,
    rawUV: [Math.round(200 * Math.sin(i / 9)) + (i % 7)],
    flags: 0,
  }))
}

export function valueSamples(values: number[], startMs = 0): EcgSample[] {
  return values.map((value, i) => ({
    timestampMs: startMs + i * STEP_MS,
    rawUV: [value],
    flags: 0,
  }))
}

/** Pedido de lote de 20 s con el modelo de señal real, para los tests. */
export function batchRequest(overrides: Partial<VestWorkerRequest> = {}): VestWorkerRequest {
  const profile = { ...DEFAULT_SIGNAL_PROFILE, seed: 5 }
  return {
    requestId: 1,
    profile,
    durationSec: 20,
    episodes: [],
    genState: initialGeneratorState(profile),
    firstSeq: 100,
    bootId: 3,
    t0Ms: 0,
    wallStartEpochMs: Date.UTC(2026, 9, 3, 15),
    simulated: true,
    ...overrides,
  }
}
