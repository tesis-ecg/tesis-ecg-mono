import { describe, expect, it } from 'vitest'

import { fleetPercent, formatTrend, readTrend, trendTone, weekdayLabel } from './chartMeta'

describe('readTrend', () => {
  it('sin período anterior no inventa un porcentaje', () => {
    // Dividir por cero daba Infinity y el chip mostraba "+Infinity%".
    const reading = readTrend({ current: 3, previous: 0 })

    expect(reading.percent).toBeNull()
    expect(formatTrend(reading)).toBe('+3')
  })

  it('una semana sin nada contra otra con algo es una caída del 100%', () => {
    expect(formatTrend(readTrend({ current: 0, previous: 4 }))).toBe('-100.0%')
  })

  it('dos semanas iguales quedan planas', () => {
    const reading = readTrend({ current: 5, previous: 5 })

    expect(reading.direction).toBe('flat')
    expect(formatTrend(reading)).toBe('0.0%')
  })
})

describe('trendTone', () => {
  it('más alertas es malo y más estudios es bueno', () => {
    // El signo no alcanza para elegir el color: depende de qué se está midiendo.
    expect(trendTone('up', 'more-is-bad')).toBe('negative')
    expect(trendTone('up', 'more-is-good')).toBe('positive')
    expect(trendTone('down', 'more-is-bad')).toBe('positive')
  })

  it('sin cambio no se pinta de nada', () => {
    expect(trendTone('flat', 'more-is-bad')).toBe('neutral')
  })
})

describe('fleetPercent', () => {
  it('sin chalecos asignados no hay porcentaje', () => {
    // 0/0 daba NaN y el medidor dibujaba un arco vacío sin explicar por qué.
    expect(fleetPercent(0, 0)).toBeNull()
  })

  it('redondea a entero', () => {
    expect(fleetPercent(3, 2)).toBe(67)
    expect(fleetPercent(4, 4)).toBe(100)
  })
})

describe('weekdayLabel', () => {
  it('no corre el día por el huso horario', () => {
    // `new Date('2026-09-04')` es medianoche UTC: en Argentina cae el 3.
    expect(weekdayLabel('2026-09-04')).toBe('vie')
  })

  it('una fecha inválida no rompe el eje', () => {
    expect(weekdayLabel('')).toBe('')
  })
})
