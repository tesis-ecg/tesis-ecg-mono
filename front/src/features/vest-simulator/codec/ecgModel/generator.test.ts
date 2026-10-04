import { describe, expect, it } from 'vitest'

import {
  FLAG_EVENT_MARKER,
  FLAG_LEAD_OFF,
  FLAG_R_PEAK,
  FLAG_SQI_MASK,
  FLAG_SQI_SHIFT,
  SAMPLE_RATE_HZ,
  SQ_BAD,
  SQ_GOOD,
} from '../frame'
import { encodeSamples } from '../riceEncoder'
import { generateEcg, initialGeneratorState, STATUS_LEAD_SIGNAL_SUSPECT } from './generator'
import { DEFAULT_SIGNAL_PROFILE, type ResolvedEpisode, type SignalProfile } from './types'

const AFTERNOON = new Date(2026, 9, 3, 15).getTime()

function profile(overrides: Partial<SignalProfile> = {}): SignalProfile {
  return { ...DEFAULT_SIGNAL_PROFILE, seed: 42, ...overrides }
}

function generate(
  durationSec: number,
  episodes: ResolvedEpisode[] = [],
  overrides: Partial<SignalProfile> = {},
  wallStartEpochMs = AFTERNOON,
) {
  const p = profile(overrides)
  return generateEcg({
    profile: p,
    durationSec,
    episodes,
    state: initialGeneratorState(p),
    startT0Ms: 0,
    wallStartEpochMs,
  })
}

/** Índices de las muestras con `R_PEAK`. */
function rPeaks(flags: number[]): number[] {
  return flags.flatMap((f, i) => (f & FLAG_R_PEAK ? [i] : []))
}

function rrSeconds(peaks: number[]): number[] {
  return peaks.slice(1).map((p, i) => (p - peaks[i]) / SAMPLE_RATE_HZ)
}

function coefficientOfVariation(values: number[]): number {
  const mean = values.reduce((a, b) => a + b, 0) / values.length
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length
  return Math.sqrt(variance) / mean
}

function framesPerSecond(overrides: Partial<SignalProfile>): number {
  const out = generate(60, [], overrides)
  return encodeSamples(out.samples).length / 60
}

describe('modelo de ECG', () => {
  it('es determinista: misma semilla y mismo estado, misma señal', () => {
    const a = generate(10).samples.map((s) => s.rawUV[0])
    const b = generate(10).samples.map((s) => s.rawUV[0])

    expect(a).toEqual(b)
  })

  it('el lote siguiente continúa la señal: sin escalón y con el ritmo intacto', () => {
    // Antes cada lote reiniciaba fase, deriva y RNG: escalón cada 10 min y
    // todos los lotes idénticos.
    const p = profile()
    const first = generateEcg({
      profile: p,
      durationSec: 30,
      episodes: [],
      state: initialGeneratorState(p),
      startT0Ms: 0,
      wallStartEpochMs: AFTERNOON,
    })
    const second = generateEcg({
      profile: p,
      durationSec: 30,
      episodes: [],
      state: first.state,
      startT0Ms: 30_000,
      wallStartEpochMs: AFTERNOON + 30_000,
    })

    const a = first.samples.map((s) => s.rawUV[0])
    const b = second.samples.map((s) => s.rawUV[0])
    expect(b).not.toEqual(a)

    // El nivel de los últimos y los primeros 20 ms coincide.
    const tail = a.slice(-10).reduce((x, y) => x + y, 0) / 10
    const head = b.slice(0, 10).reduce((x, y) => x + y, 0) / 10
    expect(Math.abs(head - tail)).toBeLessThan(400)

    // El RR que cruza el borde es uno más, no un salto de fase.
    const peaksA = rPeaks(first.samples.map((s) => s.flags))
    const peaksB = rPeaks(second.samples.map((s) => s.flags))
    const median = [...rrSeconds(peaksA)].sort((x, y) => x - y)[Math.floor(peaksA.length / 2)]
    const across = (peaksB[0] + a.length - peaksA[peaksA.length - 1]) / SAMPLE_RATE_HZ
    expect(across).toBeGreaterThan(0.7 * median)
    expect(across).toBeLessThan(1.3 * median)

    // Y el timestamp sigue la grilla de 2 ms.
    expect(second.samples[0].timestampMs).toBe(30_000)
  })

  it('marca un R_PEAK por latido, sobre el máximo local del QRS', () => {
    const out = generate(60)
    const values = out.samples.map((s) => s.rawUV[0])
    const peaks = rPeaks(out.samples.map((s) => s.flags))

    // 68 lpm de reposo más el circadiano de la tarde: del orden de 70 latidos.
    expect(peaks.length).toBeGreaterThan(55)
    expect(peaks.length).toBeLessThan(90)
    for (const p of peaks.slice(1, -1)) {
      const window = values.slice(p - 10, p + 11)
      expect(values[p]).toBeGreaterThanOrEqual(Math.max(...window) - 60)
    }
  })

  it('tiene variabilidad sinusal sin ser irregular', () => {
    const rr = rrSeconds(rPeaks(generate(120).samples.map((s) => s.flags)))
    const cv = coefficientOfVariation(rr)

    expect(cv).toBeGreaterThan(0.015)
    expect(cv).toBeLessThan(0.1)
  })

  it('baja la FC de madrugada con el ritmo circadiano', () => {
    const night = rPeaks(
      generate(120, [], {}, new Date(2026, 9, 3, 3).getTime()).samples.map((s) => s.flags),
    )
    const day = rPeaks(generate(120).samples.map((s) => s.flags))

    expect(night.length).toBeLessThan(day.length)
  })

  it('la fibrilación auricular es irregularmente irregular', () => {
    const out = generate(90, [{ kind: 'afib', startSec: 0, endSec: 90, value: 110 }])
    const rr = rrSeconds(rPeaks(out.samples.map((s) => s.flags)))

    expect(coefficientOfVariation(rr)).toBeGreaterThan(0.12)
    expect(60 / (rr.reduce((a, b) => a + b, 0) / rr.length)).toBeGreaterThan(90)
  })

  it('el bigeminismo alterna acoplamiento corto y pausa compensatoria', () => {
    const out = generate(30, [{ kind: 'pvc_bigeminy', startSec: 0, endSec: 30, value: 0 }])
    const rr = rrSeconds(rPeaks(out.samples.map((s) => s.flags))).slice(2, -2)
    const short = rr.filter((_, i) => i % 2 === 0)
    const long = rr.filter((_, i) => i % 2 === 1)
    const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length

    expect(Math.abs(mean(short) - mean(long))).toBeGreaterThan(0.3)
    // Compensatoria completa: corto + largo = dos RR sinusales.
    expect(mean(short) + mean(long)).toBeGreaterThan(1.5)
  })

  it('la pausa deja un RR del largo pedido', () => {
    const out = generate(20, [{ kind: 'pause', startSec: 8, endSec: 8, value: 3200 }])
    const rr = rrSeconds(rPeaks(out.samples.map((s) => s.flags)))

    expect(Math.max(...rr)).toBeGreaterThanOrEqual(3.15)
    expect(rr.filter((x) => x > 2).length).toBe(1)
  })

  it('el electrodo suelto se graba, se marca y lleva el SQI a no analizable', () => {
    const out = generate(30, [{ kind: 'lead_off', startSec: 10, endSec: 20, value: 0 }])
    const inside = out.samples.slice(12 * SAMPLE_RATE_HZ, 19 * SAMPLE_RATE_HZ)
    const before = out.samples.slice(2 * SAMPLE_RATE_HZ, 8 * SAMPLE_RATE_HZ)

    expect(inside.every((s) => s.flags & FLAG_LEAD_OFF)).toBe(true)
    expect(inside.every((s) => (s.flags & FLAG_SQI_MASK) >> FLAG_SQI_SHIFT === SQ_BAD)).toBe(true)
    expect(inside.some((s) => s.flags & FLAG_R_PEAK)).toBe(false)
    expect(before.every((s) => (s.flags & FLAG_SQI_MASK) >> FLAG_SQI_SHIFT === SQ_GOOD)).toBe(true)
    expect(Array.from(out.secondStatus.slice(11, 19))).toEqual(new Array(8).fill(1))
    expect(out.leadFlags & STATUS_LEAD_SIGNAL_SUSPECT).toBeTruthy()
  })

  it('un episodio que cruza el final del lote sigue en el siguiente', () => {
    const p = profile()
    const first = generateEcg({
      profile: p,
      durationSec: 20,
      episodes: [{ kind: 'lead_off', startSec: 15, endSec: 25, value: 0 }],
      state: initialGeneratorState(p),
      startT0Ms: 0,
      wallStartEpochMs: AFTERNOON,
    })
    const second = generateEcg({
      profile: p,
      durationSec: 20,
      episodes: [],
      state: first.state,
      startT0Ms: 20_000,
      wallStartEpochMs: AFTERNOON + 20_000,
    })

    expect(second.samples.slice(0, 4 * SAMPLE_RATE_HZ).every((s) => s.flags & FLAG_LEAD_OFF)).toBe(
      true,
    )
    expect(second.samples.slice(8 * SAMPLE_RATE_HZ).some((s) => s.flags & FLAG_LEAD_OFF)).toBe(
      false,
    )
  })

  it('el botón de síntoma marca una sola muestra', () => {
    const out = generate(10, [{ kind: 'symptom', startSec: 4, endSec: 4, value: 0 }])
    const marked = out.samples.flatMap((s, i) => (s.flags & FLAG_EVENT_MARKER ? [i] : []))

    expect(marked).toEqual([4 * SAMPLE_RATE_HZ])
  })

  it('lleva el offset de continua de la entrada DC-acoplada', () => {
    const values = generate(10).samples.map((s) => s.rawUV[0])
    const mean = values.reduce((a, b) => a + b, 0) / values.length

    expect(mean).toBeGreaterThan(30_000)
    expect(mean).toBeLessThan(55_000)
  })

  it('el millis() del equipo da la vuelta en 2³²', () => {
    const p = profile()
    const out = generateEcg({
      profile: p,
      durationSec: 1,
      episodes: [],
      state: initialGeneratorState(p),
      startT0Ms: 0x1_0000_0000 - 100,
      wallStartEpochMs: AFTERNOON,
    })

    expect(out.samples[0].timestampMs).toBe(0x1_0000_0000 - 100)
    expect(out.samples[50].timestampMs).toBe(0)
  })
})

describe('calibración contra las capturas de la placa', () => {
  // Tramas por segundo de las capturas reales del canal 2 pasadas por este
  // mismo codec (`../Holter-ECG-System/capturas/`): gel limpia 1,43, seco
  // limpia 1,87, seco con red de casa 3,01, al lado del router 3,90.
  it.each([
    ['gel, limpio', { electrode: 'gel', environment: 'clean' }, 1.2, 1.75],
    ['seco, limpio', { electrode: 'dry', environment: 'clean' }, 1.6, 2.2],
    ['seco, casa', { electrode: 'dry', environment: 'home' }, 2.7, 3.4],
    ['seco, router', { electrode: 'dry', environment: 'router' }, 3.5, 4.3],
  ] as const)('%s', (_, overrides, min, max) => {
    const fps = framesPerSecond(overrides)

    expect(fps).toBeGreaterThan(min)
    expect(fps).toBeLessThan(max)
  })
})
