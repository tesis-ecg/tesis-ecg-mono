import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { loadClocks, loadFleet, saveClocks, saveFleet } from './storage'
import { makeVestConfig } from './defaults'

/** `localStorage` mínimo: el entorno de test es `node` y no trae `window`. */
function stubStorage(initial: Record<string, string> = {}) {
  const store = new Map(Object.entries(initial))
  const localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  }
  vi.stubGlobal('window', { localStorage })
  return store
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('persistencia de la flota', () => {
  beforeEach(() => {
    stubStorage()
  })

  it('la API key sobrevive a un ida y vuelta', () => {
    // Es el punto entero del módulo: el backend devuelve la key en claro una
    // sola vez, así que perderla al recargar deja al chaleco con una credencial
    // muerta y 401 en cada envío.
    const config = makeVestConfig({ serial: 'HOL-0001', apiKey: 'k3y-secreta' })

    saveFleet([config])

    const [restored] = loadFleet()
    expect(restored.apiKey).toBe('k3y-secreta')
    expect(restored.serial).toBe('HOL-0001')
  })

  it('conserva el orden y la cantidad de chalecos', () => {
    const configs = [
      makeVestConfig({ label: 'Uno' }),
      makeVestConfig({ label: 'Dos' }),
      makeVestConfig({ label: 'Tres' }),
    ]

    saveFleet(configs)

    expect(loadFleet().map((c) => c.label)).toEqual(['Uno', 'Dos', 'Tres'])
  })

  it('devuelve vacío cuando no hay nada guardado', () => {
    expect(loadFleet()).toEqual([])
  })

  it('descarta las entradas corruptas sin tirar abajo las buenas', () => {
    const good = makeVestConfig({ label: 'Sirve' })
    stubStorage({
      'holter:vest-fleet': JSON.stringify([{ id: 'roto' }, good, null, 'texto suelto']),
    })

    const loaded = loadFleet()

    expect(loaded).toHaveLength(1)
    expect(loaded[0].label).toBe('Sirve')
  })

  it('tolera un JSON ilegible', () => {
    stubStorage({ 'holter:vest-fleet': '{no es json' })

    expect(loadFleet()).toEqual([])
  })

  it('tolera un objeto que no es una lista', () => {
    stubStorage({ 'holter:vest-fleet': '{"a":1}' })

    expect(loadFleet()).toEqual([])
  })

  it('no rompe si el navegador niega el storage', () => {
    // Safari en modo privado tira al escribir. Perder la persistencia no puede
    // cortar la corrida en curso.
    vi.stubGlobal('window', {
      localStorage: {
        getItem: () => {
          throw new Error('denied')
        },
        setItem: () => {
          throw new Error('denied')
        },
      },
    })

    expect(() => saveFleet([makeVestConfig()])).not.toThrow()
    expect(loadFleet()).toEqual([])
  })
})

describe('persistencia del reloj', () => {
  beforeEach(() => {
    stubStorage()
  })

  const clock = { bootId: 3, nextSeq: 162_944, t0Ms: 1200, uptimeMs: 36_000_000, batteryPct: 72 }

  it('el cursor sobrevive a un F5', () => {
    // Sin esto, recargar devolvía el equipo a `seq 0 / bootId 0`. El backend lo
    // leía como una retransmisión del estudio entero —y el estudio dejaba de
    // crecer— o, con otro bootId, aceptaba desde 0 y sobreescribía en S3 los
    // segmentos ya archivados, que se nombran con el `first_seq` del lote.
    saveClocks({ 'vest-1': clock })

    expect(loadClocks()['vest-1']).toEqual(clock)
  })

  it('devuelve vacío cuando no hay nada guardado', () => {
    expect(loadClocks()).toEqual({})
  })

  it('descarta los relojes corruptos sin tirar abajo los buenos', () => {
    stubStorage({
      'holter:vest-clocks': JSON.stringify({
        'vest-1': clock,
        'vest-2': { bootId: 1 },
        'vest-3': null,
      }),
    })

    expect(Object.keys(loadClocks())).toEqual(['vest-1'])
  })

  it('tolera un JSON ilegible y uno que no es un objeto', () => {
    stubStorage({ 'holter:vest-clocks': '{no es json' })
    expect(loadClocks()).toEqual({})

    stubStorage({ 'holter:vest-clocks': '[1,2]' })
    expect(loadClocks()).toEqual({})
  })

  it('no rompe si el navegador niega el storage', () => {
    vi.stubGlobal('window', {
      localStorage: {
        getItem: () => {
          throw new Error('denied')
        },
        setItem: () => {
          throw new Error('denied')
        },
      },
    })

    expect(() => saveClocks({ 'vest-1': clock })).not.toThrow()
    expect(loadClocks()).toEqual({})
  })
})
