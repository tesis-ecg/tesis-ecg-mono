import { afterEach, describe, expect, it, vi } from 'vitest'

import { calculateAge, formatRelativeTime } from './format'

afterEach(() => {
  vi.useRealTimers()
})

describe('formatRelativeTime', () => {
  it('usa minutos y horas antes de saltar a días', () => {
    // "hace 1 día" para algo de hace 25 horas confunde a quien está mirando si
    // su chaleco viene enviando bien.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-30T12:00:00Z'))

    expect(formatRelativeTime('2026-08-30T11:59:50Z')).toBe('recién')
    expect(formatRelativeTime('2026-08-30T11:58:00Z')).toBe('hace 2 minutos')
    expect(formatRelativeTime('2026-08-30T11:00:00Z')).toBe('hace 1 hora')
    expect(formatRelativeTime('2026-08-30T09:00:00Z')).toBe('hace 3 horas')
    expect(formatRelativeTime('2026-08-28T12:00:00Z')).toBe('hace 2 días')
  })

  it('singulariza y también mira hacia adelante', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-30T12:00:00Z'))

    expect(formatRelativeTime('2026-08-30T11:58:30Z')).toBe('hace 2 minutos')
    expect(formatRelativeTime('2026-08-30T12:30:00Z')).toBe('en 30 minutos')
  })

  it('no depende de Intl.RelativeTimeFormat', () => {
    // Hermes no lo trae: usarlo hacía explotar la pantalla de Inicio en iOS con
    // "undefined cannot be used as a constructor".
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-30T12:00:00Z'))
    const original = Reflect.get(Intl, 'RelativeTimeFormat')
    Reflect.deleteProperty(Intl, 'RelativeTimeFormat')
    try {
      expect(formatRelativeTime('2026-08-30T11:30:00Z')).toBe('hace 30 minutos')
    } finally {
      Reflect.set(Intl, 'RelativeTimeFormat', original)
    }
  })

  it('sin fecha no muestra un valor falso', () => {
    expect(formatRelativeTime(null)).toBe('Sin datos')
    expect(formatRelativeTime('no-es-una-fecha')).toBe('Sin datos')
  })
})

describe('calculateAge', () => {
  it('no cuenta el cumpleaños del año en curso si todavía no pasó', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-30T12:00:00Z'))

    expect(calculateAge('1960-08-29')).toBe(66)
    expect(calculateAge('1960-08-31')).toBe(65)
  })

  it('devuelve null sin fecha de nacimiento', () => {
    expect(calculateAge(null)).toBeNull()
  })
})
