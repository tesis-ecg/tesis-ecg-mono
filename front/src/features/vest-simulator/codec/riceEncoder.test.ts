import { describe, expect, it } from 'vitest'

import {
  CLOSE_FLUSH,
  CLOSE_GAP,
  CLOSE_RUNS,
  FLAG_EVENT_MARKER,
  FLAG_LEAD_OFF,
  FLAG_SQI_SHIFT,
  SQ_BAD,
  STEP_MS,
  readHeader,
} from './frame'
import { decodeFrame } from './riceDecoder'
import { FrameEncoder, encodeSamples, type EcgSample } from './riceEncoder'
import { DEFAULT_SIGNAL_CONFIG, generateSignal } from './signal'
import { flatSamples, valueSamples } from './testSignals'

function decodeAll(frames: Uint8Array[]): { values: number[]; flags: number[] } {
  const values: number[] = []
  const flags: number[] = []
  for (const frame of frames) {
    const decoded = decodeFrame(frame)
    values.push(...decoded.rawUV[0])
    flags.push(...decoded.flags)
  }
  return { values, flags }
}

describe('round-trip', () => {
  it('reproduce cada muestra y cada flag sin una sola diferencia', () => {
    const samples = flatSamples(3000)
    for (let i = 500; i < 900; i++) samples[i].flags = FLAG_LEAD_OFF
    samples[1200].flags = FLAG_EVENT_MARKER

    const { values, flags } = decodeAll(encodeSamples(samples))

    expect(values).toEqual(samples.map((s) => s.rawUV[0]))
    expect(flags).toEqual(samples.map((s) => s.flags))
  })

  it('sobrevive a ruido puro, que es el peor caso del predictor', () => {
    let seed = 7
    const rand = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff
      return seed / 0x7fffffff
    }
    const values = Array.from({ length: 1500 }, () => Math.round((rand() * 2 - 1) * 40_000))

    expect(decodeAll(encodeSamples(valueSamples(values))).values).toEqual(values)
  })

  it('no alimenta la media adaptativa con la primera muestra', () => {
    // Si lo hiciera, `k` se dispararía y el resto de la trama saldría mal. Es
    // el error de portabilidad que el propio INTEGRACION.md marca como el más
    // fácil de cometer.
    const values = [1_500_000, ...Array.from({ length: 160 }, (_, i) => 10 + (i % 4))]

    expect(decodeAll(encodeSamples(valueSamples(values))).values).toEqual(values)
  })

  it('conserva valores fuera del rango de int16', () => {
    // ±300 mV de offset de media celda es una condición normal del front-end
    // DC-acoplado; un int16 en µV solo llega a ±32,7 mV.
    const values = [300_000, 299_000, -300_000, -298_500, 0]

    expect(decodeAll(encodeSamples(valueSamples(values))).values).toEqual(values)
  })

  it('usa el escape de Rice ante un artefacto de fondo de escala', () => {
    const values = [...Array(10).fill(0), 1_500_000, ...Array(6).fill(0)]
    const frames = encodeSamples(valueSamples(values))

    expect(decodeAll(frames).values).toEqual(values)
    expect(readHeader(frames[0]).bitBytes).toBeGreaterThan(12)
  })

  it('maneja dos derivaciones con canal diagnóstico', () => {
    const samples: EcgSample[] = Array.from({ length: 120 }, (_, i) => ({
      timestampMs: i * STEP_MS,
      rawUV: [100 + i, -200 - i],
      diagnosticUV: [10 + i, -20 - i],
      flags: 0,
    }))

    const frames = encodeSamples(samples, { nChannels: 2, storeDiagnostic: true })
    const decoded = decodeFrame(frames[0])

    expect(decoded.header.streams).toBe(4)
    expect([...decoded.rawUV[0]]).toEqual(samples.map((s) => s.rawUV[0]))
    expect([...decoded.rawUV[1]]).toEqual(samples.map((s) => s.rawUV[1]))
    expect([...decoded.diagnosticUV[0]]).toEqual(samples.map((s) => s.diagnosticUV![0]))
  })
})

describe('cierre de trama', () => {
  it('numera las tramas de forma monótona', () => {
    const frames = encodeSamples(flatSamples(3000), { firstSeq: 100 })

    expect(frames.map((f) => readHeader(f).seq)).toEqual(frames.map((_, i) => 100 + i))
  })

  it('cierra con GAP ante un salto temporal grande', () => {
    const encoder = new FrameEncoder()
    for (let i = 0; i < 40; i++) {
      expect(encoder.addSample({ timestampMs: i * STEP_MS, rawUV: [i], flags: 0 })).toBe(true)
    }

    // 500 ms de salto: se perdieron muestras aguas arriba.
    expect(encoder.addSample({ timestampMs: 40 * STEP_MS + 500, rawUV: [0], flags: 0 })).toBe(false)

    expect(readHeader(encoder.close()!).closeReason).toBe(CLOSE_GAP)
  })

  it('permite forzar FLUSH (batería crítica)', () => {
    const encoder = new FrameEncoder()
    for (const sample of flatSamples(30)) encoder.addSample(sample)

    expect(readHeader(encoder.close(CLOSE_FLUSH)!).closeReason).toBe(CLOSE_FLUSH)
  })

  it('cierra con RUNS cuando la señal agota las corridas de flags', () => {
    const samples = flatSamples(4000)
    samples.forEach((sample, i) => {
      sample.flags = i % 2
    })

    const reasons = encodeSamples(samples).map((f) => readHeader(f).closeReason)

    expect(reasons).toContain(CLOSE_RUNS)
  })

  it('close() sin muestras devuelve null', () => {
    expect(new FrameEncoder().close()).toBeNull()
  })

  it('mide un hueco interno chico sin cerrar la trama', () => {
    const samples = flatSamples(50)
    for (let i = 25; i < 50; i++) samples[i].timestampMs += 4
    const header = readHeader(encodeSamples(samples)[0])

    expect(header.nSamples).toBe(50)
    expect(header.durationMs - (header.nSamples - 1) * STEP_MS).toBe(4)
  })
})

describe('bit de DATO SIMULADO', () => {
  it('va prendido por default: esto es un simulador de banco', () => {
    expect(readHeader(encodeSamples(flatSamples(100))[0]).simulated).toBe(true)
  })

  it('se puede apagar para probar el camino de dato clínico', () => {
    const frame = encodeSamples(flatSamples(100), { simulated: false })[0]

    expect(readHeader(frame).simulated).toBe(false)
  })
})

describe('señal generada', () => {
  it('produce latidos a la frecuencia pedida', () => {
    const { beats } = generateSignal({
      ...DEFAULT_SIGNAL_CONFIG,
      durationSec: 60,
      baseBpm: 72,
      bpmVariability: 0,
    })

    expect(beats).toBeGreaterThanOrEqual(68)
    expect(beats).toBeLessThanOrEqual(76)
  })

  it('es determinista para una misma semilla', () => {
    const config = { ...DEFAULT_SIGNAL_CONFIG, durationSec: 5, seed: 42 }

    const a = generateSignal(config).samples.map((s) => s.rawUV[0])
    const b = generateSignal(config).samples.map((s) => s.rawUV[0])

    expect(a).toEqual(b)
  })

  it('marca los tramos de lead-off y los de SQI no analizable', () => {
    const { samples } = generateSignal({
      ...DEFAULT_SIGNAL_CONFIG,
      durationSec: 10,
      leadOffSpans: [{ startSec: 2, durationSec: 3 }],
      symptomMarkersSec: [7],
    })

    const leadOff = samples.filter((s) => s.flags & FLAG_LEAD_OFF)
    expect(leadOff.length).toBeGreaterThan(0)
    expect(leadOff.every((s) => s.flags >> FLAG_SQI_SHIFT === SQ_BAD)).toBe(true)
    expect(samples.filter((s) => s.flags & FLAG_EVENT_MARKER)).toHaveLength(1)
  })

  it('la señal generada round-trippea exacto por el codec', () => {
    const { samples } = generateSignal({ ...DEFAULT_SIGNAL_CONFIG, durationSec: 8 })

    const { values, flags } = decodeAll(encodeSamples(samples))

    expect(values).toEqual(samples.map((s) => s.rawUV[0]))
    expect(flags).toEqual(samples.map((s) => s.flags))
  })
})
