import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { loadClocks, loadFleet, saveClocks, saveFleet } from './storage'
import { makeVestConfig } from './defaults'
import { initialClock } from './deviceClock'

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

  it('migra una config de la señal vieja sin perder semilla, FC ni anomalías', () => {
    const legacy = {
      ...makeVestConfig({ label: 'Vieja' }),
      signal: {
        seed: 77,
        durationSec: 600,
        sampleRateHz: 500,
        nChannels: 1,
        baseBpm: 81,
        bpmVariability: 6,
        qrsAmplitudeUV: 1100,
        noiseUV: 25,
        baselineOffsetUV: 0,
        leadOffSpans: [{ startSec: 30, durationSec: 5 }],
        rldOffSpans: [],
        saturatedSpans: [],
        unanalyzableSpans: [],
        symptomMarkersSec: [12],
      },
      network: {
        truncateBodyPct: 0,
        invalidApiKey: true,
        unknownSerial: false,
        omitUptime: false,
        maxRetries: 2,
      },
    } as Record<string, unknown>
    delete legacy.episodes
    delete legacy.pendingInjections
    stubStorage({ 'holter:vest-fleet': JSON.stringify([legacy]) })

    const [config] = loadFleet()

    expect(config.signal).toMatchObject({ seed: 77, baseBpm: 81, electrode: 'dry' })
    expect(config.episodes.map((e) => [e.kind, e.batch, e.startSec])).toEqual([
      ['lead_off', 1, 30],
      ['symptom', 1, 12],
    ])
    expect(config.pendingInjections).toEqual([])
    expect(config.network).toMatchObject({ invalidApiKey: true, postFrames: 48, graceSeconds: 60 })
    expect(config.network).not.toHaveProperty('maxRetries')
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

  const clock = {
    ...initialClock(1_757_000_000_000),
    bootId: 3,
    nextSeq: 162_944,
    t0Ms: 1200,
    bootAnchors: { 3: 1_757_000_000_000 - 30_000 },
    bootEpochMs: 1_757_000_000_000 - 30_000,
    batteryPct: 72,
    fresh: false,
  }

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

  it('migra un reloj viejo conservando la hora de la próxima muestra', () => {
    // El formato viejo mandaba `bootEpochMs + uptimeMs` como hora del puente:
    // el origen del 422. Lo que vale de él es `bootEpochMs + t0Ms`, la hora de
    // la próxima muestra, que es donde tiene que seguir la señal.
    stubStorage({
      'holter:vest-clocks': JSON.stringify({
        'vest-1': {
          bootId: 2,
          nextSeq: 5000,
          t0Ms: 1_800_000,
          uptimeMs: 2_400_000,
          bootEpochMs: 1_757_000_000_000,
          batteryPct: 80,
        },
      }),
    })

    const restored = loadClocks()['vest-1']

    expect(restored).not.toHaveProperty('uptimeMs')
    expect(restored.bootEpochMs + restored.t0Ms).toBe(1_757_000_000_000 + 1_800_000)
    expect(restored.bootAnchors).toEqual({ 2: 1_757_000_000_000 })
    expect(restored.fresh).toBe(false)
    expect(restored.genState).toBeNull()
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
