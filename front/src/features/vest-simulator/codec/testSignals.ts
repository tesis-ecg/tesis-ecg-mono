/** Señales deterministas para los tests del codec. */

import { STEP_MS } from './frame'
import type { EcgSample } from './riceEncoder'

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
