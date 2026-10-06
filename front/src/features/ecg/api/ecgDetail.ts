import { isApiError } from '@/lib/apiError'

import type { ECGDetailSource, ECGDetailWindow } from '../types'

/**
 * Un tramo contiguo de muestras que se descarga entero: un segmento por lote, o
 * el crudo completo de un estudio que no tiene segmentos.
 */
export interface DetailPiece {
  url: string
  expiresAt: string
  byteLength: number
  sha256: string | null
  startSampleIndex: number
  sampleCount: number
}

interface DetailSourceOptions {
  /** Separa a los estudios dentro de la caché compartida. */
  studyId: string
  pieces: DetailPiece[]
  download: (piece: DetailPiece, signal?: AbortSignal) => Promise<Float32Array>
  /** Las mismas piezas con URLs firmadas nuevas: las del manifest vencen. */
  refresh: () => Promise<DetailPiece[]>
  /** Hora de pared de `count` muestras desde `firstSample`, con la cuenta del resumen. */
  timestamps: (
    firstSample: number,
    count: number,
  ) => { timestampsMs: Float64Array; gapIndices: number[] }
  now?: () => number
}

/** Una URL que vence en menos que esto se renueva antes de usarla. */
const EXPIRY_MARGIN_MS = 30_000
/** Descargas simultáneas: cada una decodifica en su propio worker. */
const MAX_PARALLEL_DOWNLOADS = 4
/** ~16 MB de float32, compartidos entre todos los visores abiertos. */
const CACHE_BUDGET_SAMPLES = 4_000_000

const cache = new Map<string, Float32Array>()
let cachedSamples = 0

function cacheKey(studyId: string, piece: DetailPiece): string {
  // El sha256 identifica el contenido: si el backend reescribe una pieza, la
  // clave cambia sola. Sin hash, la posición dentro del buffer.
  return `${studyId}:${piece.sha256 ?? `${piece.startSampleIndex}+${piece.sampleCount}`}`
}

function cacheGet(key: string): Float32Array | undefined {
  const hit = cache.get(key)
  if (hit) {
    cache.delete(key)
    cache.set(key, hit)
  }
  return hit
}

function cachePut(key: string, samples: Float32Array): void {
  if (cache.has(key)) return
  cache.set(key, samples)
  cachedSamples += samples.length
  for (const [oldest, value] of cache) {
    if (cachedSamples <= CACHE_BUDGET_SAMPLES || oldest === key) break
    cache.delete(oldest)
    cachedSamples -= value.length
  }
}

/** Solo para los tests. */
export function clearDetailCache(): void {
  cache.clear()
  cachedSamples = 0
}

/**
 * Muestras por tramo, descargadas directo de S3 y no por el backend.
 *
 * Que el navegador las baje con las URLs firmadas del manifest no le cuesta
 * CPU a la API, que en el plan de Vercel es el recurso escaso. Cada pieza se
 * descarga entera una vez y queda en caché; los tramos se arman cortando de ahí.
 */
export function createEcgDetailSource(options: DetailSourceOptions): ECGDetailSource {
  const now = options.now ?? Date.now
  let pieces = sortPieces(options.pieces)
  let refreshing: Promise<void> | null = null

  const refresh = (): Promise<void> => {
    // Un solo pedido aunque varias piezas venzan juntas. Sin la señal de
    // cancelación de quien lo disparó: lo esperan también los demás.
    refreshing ??= options
      .refresh()
      .then((fresh) => {
        pieces = sortPieces(fresh)
      })
      .finally(() => {
        refreshing = null
      })
    return refreshing
  }

  const isExpiring = (piece: DetailPiece) => {
    const expiresAt = Date.parse(piece.expiresAt)
    return Number.isFinite(expiresAt) && expiresAt - now() < EXPIRY_MARGIN_MS
  }

  const fetchPiece = async (piece: DetailPiece, signal?: AbortSignal): Promise<Float32Array> => {
    const key = cacheKey(options.studyId, piece)
    const hit = cacheGet(key)
    if (hit) return hit
    let samples: Float32Array
    try {
      samples = await options.download(piece, signal)
    } catch (error) {
      // Una URL vencida contesta 403. Se renueva una vez; lo demás es un error.
      if (!isApiError(error) || error.status !== 403) throw error
      await refresh()
      const renewed = pieces.find((candidate) => samePiece(candidate, piece)) ?? piece
      samples = await options.download(renewed, signal)
    }
    cachePut(key, samples)
    return samples
  }

  const sampleCount = () => {
    const last = pieces.at(-1)
    return last ? last.startSampleIndex + last.sampleCount : 0
  }

  return {
    get sampleCount() {
      return sampleCount()
    },
    async load(startSample, endSample, signal): Promise<ECGDetailWindow> {
      const start = Math.max(0, Math.floor(startSample))
      let end = Math.min(sampleCount(), Math.ceil(endSample))
      let needed = overlapping(pieces, start, end)
      const missing = needed.filter((piece) => !cache.has(cacheKey(options.studyId, piece)))
      if (missing.some(isExpiring)) {
        await refresh()
        signal?.throwIfAborted()
        needed = overlapping(pieces, start, end)
      }

      const parts = await mapLimited(needed, MAX_PARALLEL_DOWNLOADS, (piece) =>
        fetchPiece(piece, signal),
      )
      signal?.throwIfAborted()

      // El buffer del estudio no tiene huecos, así que las piezas son
      // contiguas. Si alguna faltara, el tramo se corta ahí: unir los dos lados
      // correría la hora de todo lo que sigue.
      let cursor = start
      for (const piece of needed) {
        if (piece.startSampleIndex > cursor) {
          end = cursor
          break
        }
        cursor = Math.max(cursor, piece.startSampleIndex + piece.sampleCount)
      }
      end = Math.max(start, Math.min(end, cursor))

      const samples = new Float32Array(end - start)
      needed.forEach((piece, index) => {
        const from = Math.max(start, piece.startSampleIndex)
        const to = Math.min(end, piece.startSampleIndex + piece.sampleCount)
        if (to <= from) return
        const offset = piece.startSampleIndex
        samples.set(parts[index].subarray(from - offset, to - offset), from - start)
      })
      const { timestampsMs, gapIndices } = options.timestamps(start, samples.length)
      return { startSample: start, endSample: end, samples, timestampsMs, gapIndices }
    },
  }
}

function sortPieces(pieces: DetailPiece[]): DetailPiece[] {
  return [...pieces].sort((a, b) => a.startSampleIndex - b.startSampleIndex)
}

function samePiece(a: DetailPiece, b: DetailPiece): boolean {
  return a.startSampleIndex === b.startSampleIndex && a.sampleCount === b.sampleCount
}

function overlapping(pieces: DetailPiece[], start: number, end: number): DetailPiece[] {
  return pieces.filter(
    (piece) => piece.startSampleIndex < end && piece.startSampleIndex + piece.sampleCount > start,
  )
}

async function mapLimited<T, R>(
  items: T[],
  limit: number,
  task: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let next = 0
  const worker = async () => {
    while (next < items.length) {
      const index = next++
      results[index] = await task(items[index])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
}
