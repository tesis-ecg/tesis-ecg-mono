import { describe, expect, it } from 'vitest'
import {
  formatMeasurement,
  measureAmplitude,
  measureArea,
  measureTime,
  nearestSampleIndex,
  rrCompassMarks,
  spansGap,
  type Measurement,
  type MeasureTrace,
} from './measure'

const T0 = 1_760_000_000_000

/** Traza a 100 Hz (10 ms) con `ys` dados. */
function trace(ys: (number | null)[], gapIndices: number[] = [], step = 10): MeasureTrace {
  return { xs: ys.map((_, i) => T0 + i * step), ys, gapIndices }
}

describe('nearestSampleIndex', () => {
  const t = trace([0, 1, 2, 3])
  it('devuelve -1 en una traza vacía o con un tiempo inválido', () => {
    expect(nearestSampleIndex(trace([]), T0)).toBe(-1)
    expect(nearestSampleIndex(t, Number.NaN)).toBe(-1)
  })
  it('redondea a la muestra más cercana y satura en los bordes', () => {
    expect(nearestSampleIndex(t, T0 + 4)).toBe(0)
    expect(nearestSampleIndex(t, T0 + 6)).toBe(1)
    expect(nearestSampleIndex(t, T0 - 500)).toBe(0)
    expect(nearestSampleIndex(t, T0 + 5000)).toBe(3)
  })
  it('en un empate elige la anterior', () => {
    expect(nearestSampleIndex(t, T0 + 5)).toBe(0)
  })
})

describe('spansGap', () => {
  const t = trace([0, 1, 2, 3, 4, 5], [3])
  it('es true si entre las muestras arranca un tramo nuevo, en cualquier orden', () => {
    expect(spansGap(t, T0 + 10, T0 + 40)).toBe(true)
    expect(spansGap(t, T0 + 40, T0 + 10)).toBe(true)
    expect(spansGap(t, T0 + 20, T0 + 30)).toBe(true)
  })
  it('es false dentro de un mismo tramo', () => {
    expect(spansGap(t, T0, T0 + 20)).toBe(false)
    expect(spansGap(t, T0 + 30, T0 + 50)).toBe(false)
  })
  it('es false sin huecos o sin muestras', () => {
    expect(spansGap(trace([0, 1, 2]), T0, T0 + 20)).toBe(false)
    expect(spansGap(trace([]), T0, T0 + 20)).toBe(false)
  })
})

describe('measureTime', () => {
  it('da duración absoluta y lpm', () => {
    expect(measureTime(T0, T0 + 820)).toEqual({ durationMs: 820, rateBpm: 60000 / 820 })
  })
  it('puntos invertidos dan lo mismo', () => {
    expect(measureTime(T0 + 820, T0)).toEqual(measureTime(T0, T0 + 820))
  })
  it('duración cero no tiene frecuencia', () => {
    expect(measureTime(T0, T0)).toEqual({ durationMs: 0, rateBpm: null })
  })
})

describe('measureAmplitude', () => {
  const t = trace([0.1, 0.5, 1.5, 0.2])
  it('lee los mV de las muestras cercanas, no de los puntos', () => {
    const r = measureAmplitude(t, T0 + 4, T0 + 21)
    expect(r?.aMv).toBe(0.1)
    expect(r?.bMv).toBe(1.5)
    expect(r?.deltaMv).toBeCloseTo(1.4)
    expect(r?.durationMs).toBe(20)
  })
  it('con puntos invertidos el delta cambia de signo', () => {
    const r = measureAmplitude(t, T0 + 20, T0)
    expect(r?.aMv).toBe(1.5)
    expect(r?.deltaMv).toBeCloseTo(-1.4)
    expect(r?.durationMs).toBe(20)
  })
  it('duración cero: delta cero', () => {
    const r = measureAmplitude(t, T0 + 10, T0 + 10)
    expect(r?.deltaMv).toBe(0)
    expect(r?.durationMs).toBe(0)
  })
  it('salta una muestra NaN/null y usa la finita más cercana', () => {
    const r = measureAmplitude(trace([1, null, Number.NaN, 4]), T0 + 10, T0 + 30)
    expect(r?.aMv).toBe(1)
    expect(r?.bMv).toBe(4)
  })
  it('es null sin muestras finitas, sin traza o cruzando un hueco', () => {
    expect(measureAmplitude(trace([null, Number.NaN]), T0, T0 + 10)).toBeNull()
    expect(measureAmplitude(trace([]), T0, T0 + 10)).toBeNull()
    expect(measureAmplitude(trace([0, 1, 2, 3], [2]), T0, T0 + 30)).toBeNull()
  })
})

describe('measureArea', () => {
  it('triángulo simétrico sobre una cuerda con pendiente', () => {
    // Cuerda de 0 a 2 mV en 40 ms; el pico queda 1 mV sobre la cuerda en el medio.
    // Cuerda en t=0,10,20,30,40: 0, .5, 1, 1.5, 2. Señal: 0, 1, 2, 2.5?, ...
    const ys = [0, 0.5 + 0.5, 1 + 1, 1.5 + 0.5, 2]
    const r = measureArea(trace(ys), T0, T0 + 40)
    // desvíos: 0, .5, 1, .5, 0 -> trapecios 10 ms: (.25+.75+.75+.25)*10 = 20
    expect(r?.areaMvMs).toBeCloseTo(20)
    expect(r?.durationMs).toBe(40)
  })
  it('por debajo de la cuerda da área negativa', () => {
    const r = measureArea(trace([0, -1, 0]), T0, T0 + 20)
    expect(r?.areaMvMs).toBeCloseTo(-10)
  })
  it('puntos invertidos dan el mismo área', () => {
    const t = trace([0, 1, 2, 1, 0])
    expect(measureArea(t, T0 + 40, T0)?.areaMvMs).toBeCloseTo(measureArea(t, T0, T0 + 40)!.areaMvMs)
  })
  it('es null con duración cero o una sola muestra', () => {
    expect(measureArea(trace([0, 1, 2]), T0 + 10, T0 + 12)).toBeNull()
    expect(measureArea(trace([1]), T0, T0 + 50)).toBeNull()
  })
  it('es null si cruza un hueco', () => {
    expect(measureArea(trace([0, 1, 2, 1, 0], [2]), T0, T0 + 40)).toBeNull()
  })
  it('un NaN interior se salta y se une entre las vecinas finitas', () => {
    const r = measureArea(trace([0, Number.NaN, 0]), T0, T0 + 20)
    expect(r?.areaMvMs).toBe(0)
  })
  it('es null si los extremos no tienen muestra finita', () => {
    expect(measureArea(trace([null, null, null]), T0, T0 + 20)).toBeNull()
  })
})

describe('rrCompassMarks', () => {
  it('marca a los dos lados de a dentro del rango', () => {
    const r = rrCompassMarks(T0 + 1000, T0 + 1800, T0, T0 + 4000)
    expect(r.intervalMs).toBe(800)
    expect(r.rateBpm).toBe(75)
    expect(r.marksMs).toEqual([T0 + 200, T0 + 1000, T0 + 1800, T0 + 2600, T0 + 3400])
  })
  it('funciona con puntos invertidos', () => {
    const r = rrCompassMarks(T0 + 1800, T0 + 1000, T0, T0 + 4000)
    expect(r.intervalMs).toBe(800)
    expect(r.marksMs).toContain(T0 + 1800)
    expect(r.marksMs).toContain(T0 + 200)
  })
  it('limita a maxMarks conservando las más cercanas a a', () => {
    const r = rrCompassMarks(T0 + 5000, T0 + 5010, T0, T0 + 10_000, 5)
    expect(r.marksMs).toHaveLength(5)
    expect(r.marksMs).toContain(T0 + 5000)
    expect(r.marksMs).toEqual([...r.marksMs].sort((x, y) => x - y))
  })
  it('el tope por defecto es 200', () => {
    expect(rrCompassMarks(T0, T0 + 1, T0, T0 + 100_000).marksMs).toHaveLength(200)
  })
  it('intervalo cero o rango invertido: sin marcas', () => {
    expect(rrCompassMarks(T0, T0, T0, T0 + 1000)).toEqual({
      intervalMs: 0,
      rateBpm: null,
      marksMs: [],
    })
    expect(rrCompassMarks(T0, T0 + 500, T0 + 1000, T0).marksMs).toEqual([])
  })
  it('a fuera del rango: solo las marcas que caen adentro', () => {
    const r = rrCompassMarks(T0 - 10_000, T0 - 9_500, T0, T0 + 1000)
    expect(r.marksMs).toEqual([T0, T0 + 500, T0 + 1000])
  })
})

describe('formatMeasurement', () => {
  const base = { id: 'm', aMs: T0, bMs: T0 + 20 }
  const t = trace([0.1, 0.5, 1.34])
  const m = (tool: Measurement['tool'], over: Partial<Measurement> = {}): Measurement => ({
    ...base,
    tool,
    ...over,
  })

  it('tiempo con lpm, redondeado', () => {
    expect(formatMeasurement(m('time', { bMs: T0 + 412 }), null)).toBe('412 ms · 146 lpm')
  })
  it('R-R', () => {
    expect(formatMeasurement(m('rr', { bMs: T0 + 820 }), null)).toBe('R-R 820 ms · 73 lpm')
  })
  it('amplitud con coma decimal', () => {
    expect(formatMeasurement(m('amplitude'), t)).toBe('Δ 1,24 mV')
  })
  it('área con coma decimal', () => {
    // desvíos 0, 0.4 - 0.72 = ... se verifica contra measureArea
    const area = measureArea(t, T0, T0 + 20)!.areaMvMs
    expect(formatMeasurement(m('area'), t)).toBe(`Área ${area.toFixed(1).replace('.', ',')} µV·s`)
  })
  it('"—" cuando no se puede medir', () => {
    expect(formatMeasurement(m('amplitude'), null)).toBe('—')
    expect(formatMeasurement(m('area'), trace([0, 1, 2], [1]))).toBe('—')
    expect(formatMeasurement(m('time', { bMs: T0 }), null)).toBe('—')
    expect(formatMeasurement(m('rr', { bMs: T0 + 40 }), trace([0, 1, 2, 3, 4], [2]))).toBe('—')
  })
  it('no muestra -0,00', () => {
    expect(formatMeasurement(m('amplitude'), trace([1, 1.0001]))).toBe('Δ 0,00 mV')
  })
})
