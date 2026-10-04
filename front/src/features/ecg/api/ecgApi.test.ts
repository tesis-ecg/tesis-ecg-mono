import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'

import { getStudyEcg } from './ecgApi'

describe('getStudyEcg', () => {
  const originalFetch = globalThis.fetch
  const originalWorker = globalThis.Worker

  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.fetch = originalFetch
    globalThis.Worker = originalWorker
  })

  it('no consulta el endpoint legacy cuando el estudio no tiene ECG', async () => {
    const error = createApiError({
      status: 404,
      code: 'NOT_FOUND',
      serverCode: 'ECG_NOT_FOUND',
      message: 'ECG no disponible para este estudio.',
    })
    const getSpy = vi.spyOn(api, 'get').mockRejectedValue(error)

    await expect(getStudyEcg('study-id')).rejects.toBe(error)
    expect(getSpy).toHaveBeenCalledOnce()
    expect(getSpy).toHaveBeenCalledWith('/studies/study-id/ecg/manifest', {
      signal: undefined,
    })
  })

  it('usa el endpoint legacy si el backend no reconoce la ruta de manifest', async () => {
    const routeNotFound = createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Recurso no encontrado.',
    })
    const legacyFailure = new Error('legacy request reached')
    const getSpy = vi
      .spyOn(api, 'get')
      .mockRejectedValueOnce(routeNotFound)
      .mockRejectedValueOnce(legacyFailure)

    await expect(getStudyEcg('study-id')).rejects.toBe(legacyFailure)
    expect(getSpy).toHaveBeenCalledTimes(2)
    expect(getSpy).toHaveBeenNthCalledWith(2, '/studies/study-id/ecg', {
      signal: undefined,
    })
  })

  it('comprime huecos de pared usando la duración derivada de las muestras', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1000,
        sampleRate: 500,
        durationMs: 3 * 60 * 60 * 1000,
        levels: [level({ samplesPerBucket: 256, pointCount: 4 })],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.sampleRate).toBe(500)
    expect(signal.durationMs).toBe(2000)
  })

  it('no estira un nivel incompleto hasta el final del estudio', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1600,
        levels: [level({ samplesPerBucket: 16, pointCount: 100, byteLength: 400 })],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse(Array(100).fill(1))) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.metadata?.processedSampleCount).toBe(800)
    // Sin tramos, la hora sale de la frecuencia: 800 muestras a 500 Hz.
    expect(signal.metadata?.processedEndMs).toBe(1_700_000_001_600)
    expect(signal.durationMs).toBe(3200)
    expect(signal.timestampsMs[99] - signal.timestampsMs[0]).toBe(1584)
  })

  it('no informa tramo sin procesar cuando el nivel cubre todo el estudio', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1600,
        levels: [level({ samplesPerBucket: 16, pointCount: 200, byteLength: 800 })],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse(Array(200).fill(1))) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.metadata?.processedSampleCount).toBe(1600)
    expect(signal.metadata?.processedEndMs).toBeUndefined()
  })

  it('ubica el final procesado en la hora de pared de su tramo', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1600,
        startTimestamp: start,
        levels: [level({ samplesPerBucket: 16, pointCount: 100, byteLength: 400 })],
        timeline: [
          timelineSegment(0, 0, 500, start, start + 1000),
          // Segundo tramo después de un hueco de una hora.
          timelineSegment(1, 500, 1100, start + 3_601_000, start + 3_603_200),
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse(Array(100).fill(1))) as typeof fetch

    const signal = await getStudyEcg('study-id')

    // La muestra 800 es la 300 del segundo tramo: 300 × 2 ms después de su inicio.
    expect(signal.metadata?.processedEndMs).toBe(start + 3_601_000 + 600)
  })

  it('no tapa puntos dibujados cuando las anclas de dos tramos se solapan', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1600,
        startTimestamp: start,
        levels: [level({ samplesPerBucket: 16, pointCount: 68, byteLength: 272 })],
        timeline: [
          timelineSegment(0, 0, 500, start, start + 1000),
          timelineSegment(1, 500, 1100, start + 800, start + 3000),
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse(Array(68).fill(1))) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.metadata?.processedSampleCount).toBe(544)
    expect(signal.timestampsMs.at(-1)).toBe(start + 992)
    expect(signal.metadata?.processedEndMs).toBe(start + 992)
  })

  it('marca la hora como no verificada si un tramo posterior no tiene bootId del ancla', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 4,
        startTimestamp: start,
        startTimeVerified: true,
        levels: [level({ pointCount: 4 })],
        timeline: [
          {
            ordinal: 0,
            startSampleIndex: 0,
            sampleCount: 2,
            startEpochMs: start,
            endEpochMs: start + 4,
            anchorMatchesBoot: true,
          },
          {
            ordinal: 1,
            startSampleIndex: 2,
            sampleCount: 2,
            startEpochMs: start + 1000,
            endEpochMs: start + 1004,
            anchorMatchesBoot: null,
          },
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.metadata?.startTimeVerified).toBe(false)
  })

  it('concatena en orden los segmentos de un estudio demasiado corto para la pirámide', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 3,
        levels: [],
        segments: [
          object({ url: 'second', startSampleIndex: 2, sampleCount: 1 }),
          object({ url: 'first', startSampleIndex: 0, sampleCount: 2 }),
        ],
      }),
    })
    globalThis.fetch = vi.fn(async (input) =>
      floatResponse(String(input) === 'first' ? [1, 2] : [3]),
    ) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect([...signal.samples]).toEqual([1, 2, 3])
    expect(signal.durationMs).toBe(6)
  })

  it('convierte las anotaciones relativas del manifest a timestamps absolutos', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 4,
        levels: [level({ samplesPerBucket: 16, pointCount: 4 })],
        annotations: [
          {
            id: 'event-1',
            kind: 'lead_off',
            category: 'signal_quality',
            severity: 'medium',
            startOffsetMs: 1000,
            endOffsetMs: 1500,
            confidenceScore: null,
          },
          // La respuesta del paciente al aviso de ese hallazgo: el vínculo es
          // lo que después deja etiquetarla y resaltarlas juntas.
          {
            id: 'report-1',
            kind: 'patient_report',
            category: 'patient_marker',
            severity: 'high',
            startOffsetMs: 1250,
            endOffsetMs: 1250,
            confidenceScore: null,
            linkedAnnotationId: 'event-1',
            description: 'Palpitaciones',
          },
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.annotations).toEqual([
      {
        id: 'event-1',
        kind: 'lead_off',
        category: 'signal_quality',
        severity: 'medium',
        startMs: 1_700_000_001_000,
        endMs: 1_700_000_001_500,
        confidenceScore: null,
        // Un manifest sin los campos nuevos (backend viejo) no puede dejar
        // `undefined` sueltos: el visor los lee siempre.
        linkedAnnotationId: null,
        description: null,
      },
      {
        id: 'report-1',
        kind: 'patient_report',
        category: 'patient_marker',
        severity: 'high',
        startMs: 1_700_000_001_250,
        endMs: 1_700_000_001_250,
        confidenceScore: null,
        linkedAnnotationId: 'event-1',
        description: 'Palpitaciones',
      },
    ])
  })
})

function timelineSegment(
  ordinal: number,
  startSampleIndex: number,
  sampleCount: number,
  startEpochMs: number,
  endEpochMs: number,
) {
  return {
    ordinal,
    startSampleIndex,
    sampleCount,
    startEpochMs,
    endEpochMs,
    bootId: 1,
    anchorSource: 'ntp',
    anchorUncertaintyMs: 10,
    anchorMatchesBoot: true,
  }
}

/** Un nivel de la pirámide, que desde el manifest v3 llega en chunks. */
function level(overrides: Record<string, unknown> = {}) {
  const { samplesPerBucket = 16, pointCount = 4, ...rest } = overrides
  return {
    samplesPerBucket,
    pointCount,
    encoding: 'minmax-float32-le',
    chunks: [object({ pointCount, ...rest })],
  }
}

function object(overrides: Record<string, unknown> = {}) {
  return {
    url: 'level',
    expiresAt: '2026-01-01T00:00:00Z',
    byteLength: 16,
    sha256: null,
    ...overrides,
  }
}

function manifest(overrides: Record<string, unknown> = {}) {
  return {
    formatVersion: 3,
    encoding: 'float32-le',
    sampleRate: 500,
    sampleCount: 1000,
    startTimestamp: 1_700_000_000_000,
    durationMs: 2000,
    raw: null,
    levels: [],
    segments: [],
    ...overrides,
  }
}

function floatResponse(values: number[]): Response {
  return new Response(new Float32Array(values).buffer, { status: 200 })
}

function installDecoderWorker() {
  class DecoderWorker {
    onerror: (() => void) | null = null
    onmessage: ((event: MessageEvent) => void) | null = null

    postMessage(message: { buffer: ArrayBuffer }) {
      this.onmessage?.({ data: { ok: true, samples: message.buffer } } as MessageEvent)
    }

    terminate() {}
  }
  globalThis.Worker = DecoderWorker as unknown as typeof Worker
}

describe('línea de tiempo de pared', () => {
  const originalFetch = globalThis.fetch
  const originalWorker = globalThis.Worker

  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.fetch = originalFetch
    globalThis.Worker = originalWorker
  })

  it('el eje refleja el hueco en vez de pegar los bordes', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    const hourMs = 3_600_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 4,
        sampleRate: 500,
        startTimestamp: start,
        segments: [object({ startSampleIndex: 0, sampleCount: 4 })],
        // El chaleco grabó dos muestras, estuvo una hora apagado y grabó dos más.
        timeline: [
          {
            ordinal: 0,
            startSampleIndex: 0,
            sampleCount: 2,
            startEpochMs: start,
            endEpochMs: start + 4,
            bootId: 1,
            anchorSource: 'ntp',
            anchorUncertaintyMs: 50,
          },
          {
            ordinal: 1,
            startSampleIndex: 2,
            sampleCount: 2,
            startEpochMs: start + hourMs,
            endEpochMs: start + hourMs + 4,
            bootId: 2,
            anchorSource: 'ntp',
            anchorUncertaintyMs: 50,
          },
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    // El eje va de la primera muestra al final de la última: 8 ms grabados más
    // los 3.599.996 ms de silencio entre el fin del primer tramo y el inicio del
    // segundo. Antes esto daba 8 — el hueco no ocupaba nada y toda la señal
    // posterior quedaba fechada una hora antes de cuando se midió.
    expect(signal.durationMs).toBe(hourMs + 4)
    expect(signal.timeline).toHaveLength(2)
    // La primera muestra después del corte lleva su hora real, no la continuación.
    expect(signal.timestampsMs[2]).toBe(start + hourMs)
    // Y la traza se corta ahí, para no unir dos instantes que nunca fueron contiguos.
    expect(signal.gapIndices).toEqual([2])
  })

  it('dos tramos cuyas anclas se pisan no mandan el eje para atrás', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 4,
        sampleRate: 500,
        startTimestamp: start,
        segments: [object({ startSampleIndex: 0, sampleCount: 4 })],
        timeline: [
          {
            ordinal: 0,
            startSampleIndex: 0,
            sampleCount: 2,
            startEpochMs: start,
            endEpochMs: start + 4,
            bootId: 1,
            anchorSource: 'server_receive',
            anchorUncertaintyMs: 7000,
          },
          {
            // Mismo instante real, otra ancla: con `server_receive` la hora se
            // deriva de la recepción del backend y arrastra la latencia del
            // pedido, así que dos tramos contiguos pueden discrepar en segundos
            // y el segundo arrancar ANTES de que termine el primero.
            ordinal: 1,
            startSampleIndex: 2,
            sampleCount: 2,
            startEpochMs: start - 5_000,
            endEpochMs: start - 5_000 + 4,
            bootId: 2,
            anchorSource: 'server_receive',
            anchorUncertaintyMs: 7000,
          },
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    // uPlot hace búsqueda binaria sobre el eje X: un solo punto fuera de orden
    // le rompe el cursor y el dibujo.
    for (let i = 1; i < signal.timestampsMs.length; i++) {
      expect(signal.timestampsMs[i]).toBeGreaterThanOrEqual(signal.timestampsMs[i - 1])
    }
  })

  it('la duración alcanza para llegar al último punto del eje', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 4,
        sampleRate: 500,
        startTimestamp: start,
        segments: [object({ startSampleIndex: 0, sampleCount: 4 })],
        timeline: [
          {
            ordinal: 0,
            startSampleIndex: 0,
            sampleCount: 4,
            startEpochMs: start,
            // Un tramo que en hora de pared dura MÁS que sus muestras: adentro
            // de las tramas faltaron milisegundos de señal (`internal_gap_ms`).
            // El recuento de muestras no los tiene y la hora de pared sí.
            endEpochMs: start + 60_000,
            bootId: 1,
            anchorSource: 'ntp',
            anchorUncertaintyMs: 50,
          },
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    // El visor clampea el scroll a `durationMs`. Si se queda corto respecto del
    // eje que dibuja, el final del estudio queda fuera de alcance.
    const lastSec =
      (signal.timestampsMs[signal.timestampsMs.length - 1] - signal.startTimestamp) / 1000
    expect(signal.durationMs / 1000).toBeGreaterThanOrEqual(lastSec)
  })

  it('un estudio sin tramos conserva el eje uniforme de siempre', async () => {
    installDecoderWorker()
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 1000,
        sampleRate: 500,
        levels: [level({ samplesPerBucket: 256, pointCount: 4 })],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.gapIndices).toEqual([])
    expect(signal.timeline).toEqual([])
    expect(signal.durationMs).toBe(2000)
  })

  it('prefiere la hora absoluta que resuelve el backend para las anotaciones', async () => {
    installDecoderWorker()
    const start = 1_700_000_000_000
    vi.spyOn(api, 'get').mockResolvedValue({
      data: manifest({
        sampleCount: 4,
        startTimestamp: start,
        levels: [level({ samplesPerBucket: 16, pointCount: 4 })],
        annotations: [
          {
            id: 'event-1',
            kind: 'afib',
            category: 'clinical',
            severity: 'high',
            startOffsetMs: 1000,
            endOffsetMs: 1500,
            // El backend ya resolvió esto contra la línea de tiempo: cae después
            // de un hueco, así que no es `startTimestamp + offset`.
            startEpochMs: start + 3_601_000,
            endEpochMs: start + 3_601_500,
            confidenceScore: null,
          },
        ],
      }),
    })
    globalThis.fetch = vi.fn(async () => floatResponse([1, 2, 3, 4])) as typeof fetch

    const signal = await getStudyEcg('study-id')

    expect(signal.annotations[0].startMs).toBe(start + 3_601_000)
    expect(signal.annotations[0].endMs).toBe(start + 3_601_500)
  })
})
