import { api } from '@/lib/api'
import { createApiError, isApiError } from '@/lib/apiError'

import type { ECGAnnotationCategory, ECGAnnotationSeverity, ECGSignal } from '../types'

interface EcgUrlResponse {
  url: string
  sampleRate: number
  startTimestamp: number
  durationMs: number
  sampleCount: number
}

interface EcgObject {
  url: string
  expiresAt: string
  byteLength: number
  sha256: string | null
}

interface EcgLevelChunk extends EcgObject {
  pointCount: number
}

interface EcgLevel {
  samplesPerBucket: number
  pointCount: number
  encoding: 'minmax-float32-le'
  /**
   * Un nivel llega en chunks: cada lote del chaleco anexa el suyo. Antes era un
   * objeto único que el backend reescribía entero en cada lote, y eso crecía con
   * el estudio hasta tumbar la ingesta. Concatenados en orden dan el nivel.
   */
  chunks: EcgLevelChunk[]
}

interface EcgSegment extends EcgObject {
  startSampleIndex: number
  sampleCount: number
}

interface EcgTimelineSegment {
  ordinal: number
  startSampleIndex: number
  sampleCount: number
  startEpochMs: number
  endEpochMs: number
  bootId: number | null
  anchorSource: 'ntp' | 'none' | 'server_receive'
  anchorUncertaintyMs: number | null
}

interface EcgAnnotation {
  id: string
  kind: string
  category: ECGAnnotationCategory
  severity: ECGAnnotationSeverity
  startOffsetMs: number
  endOffsetMs: number
  startEpochMs?: number
  endEpochMs?: number
  confidenceScore: number | null
  linkedAnnotationId?: string | null
  description?: string | null
}

/**
 * Manifest v2. Dos formas de estudio conviven:
 *
 * - **Seedeado / legacy**: toda la señal en `raw`, `segments` vacío.
 * - **Ingestado desde el chaleco**: `raw` en `null` y la señal repartida en
 *   `segments`, uno por lote horario.
 *
 * El visor usa `levels` en los dos casos, así que la diferencia casi no lo
 * afecta: `raw` solo entra en juego cuando el estudio es tan corto que no
 * generó ningún nivel de pirámide.
 */
interface EcgManifest {
  formatVersion: 1 | 2 | 3
  encoding: 'float32-le'
  sampleRate: number
  sampleCount: number
  startTimestamp: number
  durationMs: number
  status?: string
  isSimulated?: boolean
  raw: EcgObject | null
  levels: EcgLevel[]
  segments?: EcgSegment[]
  timeline?: EcgTimelineSegment[]
  annotations?: EcgAnnotation[]
}

const MAX_INITIAL_POINTS = 20_000
const MAX_LEGACY_BYTES = 5 * 1024 * 1024

export async function getStudyEcg(studyId: string, signal?: AbortSignal): Promise<ECGSignal> {
  let manifest: EcgManifest
  try {
    const response = await api.get<EcgManifest>(`/studies/${studyId}/ecg/manifest`, { signal })
    manifest = response.data
  } catch (error) {
    const isMissingManifestRoute =
      isApiError(error) &&
      error.code === 'NOT_FOUND' &&
      error.serverCode !== 'ECG_NOT_FOUND' &&
      error.serverCode !== 'STUDY_NOT_FOUND'
    if (isMissingManifestRoute) {
      return getStudyEcgLegacy(studyId, signal)
    }
    throw error
  }
  const eligibleLevels = manifest.levels
    .filter((level) => level.pointCount <= MAX_INITIAL_POINTS)
    .sort((a, b) => b.pointCount - a.pointCount)
  const level =
    eligibleLevels[0] ?? [...manifest.levels].sort((a, b) => a.pointCount - b.pointCount)[0]
  const source = level ? null : manifest.raw
  const segments = [...(manifest.segments ?? [])].sort(
    (a, b) => a.startSampleIndex - b.startSampleIndex,
  )
  if (!level && !source && segments.length === 0) {
    // Estudio ingestado cuyo primer lote todavía no terminó de procesarse: hay
    // fila pero no hay ni pirámide ni blob completo. No es un error del cliente.
    throw createApiError({
      status: 503,
      code: 'SERVER',
      message: 'El ECG de este estudio todavía se está procesando.',
    })
  }
  const fallbackBytes =
    source?.byteLength ?? segments.reduce((total, item) => total + item.byteLength, 0)
  if (!level && fallbackBytes > MAX_LEGACY_BYTES) {
    throw createApiError({
      status: 503,
      code: 'SERVER',
      message: 'El ECG todavía no tiene una vista optimizada disponible.',
    })
  }

  const samples = level
    ? await downloadLevel(level, signal)
    : source
      ? await downloadEcgObject(source, signal)
      : await downloadSegments(segments, signal)

  const timeline = [...(manifest.timeline ?? [])].sort((a, b) => a.ordinal - b.ordinal)
  const { timestampsMs, gapIndices } = buildTimestamps(
    samples.length,
    manifest.sampleCount,
    manifest.sampleRate,
    manifest.startTimestamp,
    timeline,
  )
  const startTimestamp = timestampsMs.length > 0 ? timestampsMs[0] : manifest.startTimestamp

  return {
    sampleRate: manifest.sampleRate,
    // El largo del eje, medido sobre el eje mismo: la hora del último punto menos
    // la del primero. Incluye los huecos, que es lo que hace que un corte se vea
    // como un corte, pero además **coincide** con lo que dibuja `buildXAxis`.
    //
    // Sumar los huecos al tiempo grabado daba otro número: la duración de un
    // tramo en hora de pared también lleva adentro los milisegundos que faltan
    // DENTRO de una trama (`FrameInfo.internal_gap_ms`) y la corrección de
    // deriva, y ninguna de las dos cosas está en el recuento de muestras. El
    // visor clampea el scroll a `durationMs`, así que quedarse corto dejaba el
    // final del estudio fuera de alcance.
    durationMs: wallClockSpanMs(timestampsMs, manifest),
    samples,
    startTimestamp,
    timestampsMs,
    gapIndices,
    timeline,
    annotations: (manifest.annotations ?? []).map((annotation) => ({
      id: annotation.id,
      kind: annotation.kind,
      category: annotation.category,
      severity: annotation.severity,
      // El backend resuelve la hora real contra la línea de tiempo. El fallback
      // por offset es para los estudios legacy, que no tienen huecos y donde las
      // dos cuentas dan lo mismo.
      startMs: annotation.startEpochMs ?? manifest.startTimestamp + annotation.startOffsetMs,
      endMs: annotation.endEpochMs ?? manifest.startTimestamp + annotation.endOffsetMs,
      confidenceScore: annotation.confidenceScore,
      linkedAnnotationId: annotation.linkedAnnotationId ?? null,
      description: annotation.description ?? null,
    })),
  }
}

/**
 * Hora de pared de cada punto dibujado, y dónde se corta la traza.
 *
 * El array descargado puede ser la señal cruda o un nivel de la pirámide, así
 * que un punto no es necesariamente una muestra: se reparten uniformemente sobre
 * el espacio de muestras del estudio y de ahí se traduce a hora con la línea de
 * tiempo. Sin línea de tiempo —estudio seedeado o legacy— se cae al eje
 * uniforme de siempre, que para una grabación sin cortes es correcto.
 */
function buildTimestamps(
  pointCount: number,
  sampleCount: number,
  sampleRate: number,
  fallbackStartMs: number,
  timeline: EcgTimelineSegment[],
): { timestampsMs: Float64Array; gapIndices: number[] } {
  const timestampsMs = new Float64Array(pointCount)
  const gapIndices: number[] = []
  if (pointCount === 0) return { timestampsMs, gapIndices }

  const samplesPerPoint = pointCount > 0 ? sampleCount / pointCount : 1
  if (timeline.length === 0 || sampleRate <= 0) {
    const dt = pointCount > 0 ? recordingDurationMs(sampleCount, sampleRate) / pointCount : 0
    for (let i = 0; i < pointCount; i++) timestampsMs[i] = fallbackStartMs + i * dt
    return { timestampsMs, gapIndices }
  }

  let cursor = 0
  let previousOrdinal = timeline[0].ordinal
  let previousMs = -Infinity
  for (let i = 0; i < pointCount; i++) {
    const sample = i * samplesPerPoint
    while (
      cursor + 1 < timeline.length &&
      sample >= timeline[cursor].startSampleIndex + timeline[cursor].sampleCount
    ) {
      cursor++
    }
    const segment = timeline[cursor]
    const within = Math.max(sample - segment.startSampleIndex, 0)
    // Nunca hacia atrás. Cada tramo trae su propia ancla, y dos anclas contiguas
    // pueden discrepar por su incertidumbre — con `anchorSource: 'server_receive'`
    // esa incertidumbre son segundos. uPlot hace búsqueda binaria sobre el eje X
    // y asume que está ordenado: un solo punto fuera de orden le rompe el cursor
    // y el dibujo. Que dos tramos se solapen unos milisegundos es ruido del
    // ancla, no señal, y aplastarlo es preferible a un gráfico roto.
    previousMs = Math.max(segment.startEpochMs + (within * 1000) / sampleRate, previousMs)
    timestampsMs[i] = previousMs
    if (segment.ordinal !== previousOrdinal) {
      gapIndices.push(i)
      previousOrdinal = segment.ordinal
    }
  }
  return { timestampsMs, gapIndices }
}

export async function getStudyEcgLegacy(studyId: string, signal?: AbortSignal): Promise<ECGSignal> {
  const { data: meta } = await api.get<EcgUrlResponse>(`/studies/${studyId}/ecg`, { signal })

  if (meta.sampleCount * 4 > MAX_LEGACY_BYTES) {
    throw createApiError({ status: 503, code: 'SERVER', message: 'ECG demasiado grande.' })
  }
  const response = await fetch(meta.url, { signal })
  if (!response.ok) throwDownloadError(response.status)

  const buffer = await response.arrayBuffer()
  const expectedBytes = meta.sampleCount * 4
  if (buffer.byteLength !== expectedBytes) {
    throw createApiError({
      status: 500,
      code: 'SERVER',
      message: 'Los datos del ECG están corruptos o incompletos.',
    })
  }

  return {
    sampleRate: meta.sampleRate,
    durationMs: recordingDurationMs(meta.sampleCount, meta.sampleRate),
    samples: await decodeEcgObject(buffer, { byteLength: expectedBytes, sha256: null }, signal),
    startTimestamp: meta.startTimestamp,
    // El camino legacy no tiene línea de tiempo: su señal es un blob único sin
    // huecos, así que el eje uniforme de siempre es correcto.
    ...uniformTimeline(meta.sampleCount, meta.sampleCount, meta.sampleRate, meta.startTimestamp),
    annotations: [],
  }
}

/** Eje uniforme para los estudios que no tienen tramos (seedeados y legacy). */
export function uniformTimeline(
  pointCount: number,
  sampleCount: number,
  sampleRate: number,
  startMs: number,
): { timestampsMs: Float64Array; gapIndices: number[]; timeline: [] } {
  const timestampsMs = new Float64Array(pointCount)
  const dt = pointCount > 0 ? recordingDurationMs(sampleCount, sampleRate) / pointCount : 0
  for (let i = 0; i < pointCount; i++) timestampsMs[i] = startMs + i * dt
  return { timestampsMs, gapIndices: [], timeline: [] }
}

/**
 * Largo del eje: de la hora del primer punto a la del último, más lo que ocupa
 * ese último punto.
 *
 * El `+ perPoint` no es un ajuste cosmético. N puntos que cubren D ms arrancan en
 * 0 y terminan en `(N−1)·D/N`, no en `D`: sin él, la duración de un estudio
 * legacy se acortaría un punto. Con él, un eje uniforme da exactamente el tiempo
 * grabado, que es lo que valía antes, y uno con tramos da el tiempo grabado más
 * los huecos.
 */
function wallClockSpanMs(
  timestampsMs: Float64Array,
  manifest: { sampleCount: number; sampleRate: number },
): number {
  const recordedMs = recordingDurationMs(manifest.sampleCount, manifest.sampleRate)
  if (timestampsMs.length === 0) return recordedMs
  const perPointMs = recordedMs / timestampsMs.length
  return timestampsMs[timestampsMs.length - 1] - timestampsMs[0] + perPointMs
}

export function recordingDurationMs(sampleCount: number, sampleRate: number): number {
  return sampleRate > 0 ? (sampleCount / sampleRate) * 1000 : 0
}

/** Un nivel llega en chunks; concatenarlos en orden reconstruye el nivel. */
async function downloadLevel(level: EcgLevel, signal?: AbortSignal): Promise<Float32Array> {
  const parts = await Promise.all(level.chunks.map((chunk) => downloadEcgObject(chunk, signal)))
  const total = parts.reduce((sum, part) => sum + part.length, 0)
  const points = new Float32Array(total)
  let offset = 0
  for (const part of parts) {
    points.set(part, offset)
    offset += part.length
  }
  return points
}

async function downloadEcgObject(source: EcgObject, signal?: AbortSignal): Promise<Float32Array> {
  const response = await fetch(source.url, { signal })
  if (!response.ok) throwDownloadError(response.status)
  return decodeEcgObject(await response.arrayBuffer(), source, signal)
}

async function downloadSegments(
  segments: EcgSegment[],
  signal?: AbortSignal,
): Promise<Float32Array> {
  const parts = await Promise.all(segments.map((segment) => downloadEcgObject(segment, signal)))
  const totalSamples = segments.reduce((total, segment) => total + segment.sampleCount, 0)
  const samples = new Float32Array(totalSamples)
  let offset = 0
  for (let index = 0; index < parts.length; index++) {
    const part = parts[index]
    if (part.length !== segments[index].sampleCount) {
      throw createApiError({
        status: 500,
        code: 'SERVER',
        message: 'Los segmentos del ECG son inconsistentes.',
      })
    }
    samples.set(part, offset)
    offset += part.length
  }
  return samples
}

function throwDownloadError(status: number): never {
  throw createApiError({
    status,
    code: 'UNKNOWN',
    message: 'No se pudo descargar el ECG del estudio.',
  })
}

async function decodeEcgObject(
  buffer: ArrayBuffer,
  source: Pick<EcgObject, 'byteLength' | 'sha256'>,
  signal?: AbortSignal,
): Promise<Float32Array> {
  signal?.throwIfAborted()
  const worker = new Worker(new URL('../workers/ecgDecoder.worker.ts', import.meta.url), {
    type: 'module',
  })

  return await new Promise<Float32Array>((resolve, reject) => {
    const cleanup = () => {
      signal?.removeEventListener('abort', onAbort)
      worker.terminate()
    }
    const onAbort = () => {
      cleanup()
      reject(signal?.reason ?? new DOMException('Request aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
    worker.onerror = () => {
      cleanup()
      reject(
        createApiError({
          status: 500,
          code: 'SERVER',
          message: 'No se pudo decodificar el ECG.',
        }),
      )
    }
    worker.onmessage = (
      event: MessageEvent<{ ok: true; samples: ArrayBuffer } | { ok: false; message: string }>,
    ) => {
      cleanup()
      if (!event.data.ok) {
        reject(createApiError({ status: 500, code: 'SERVER', message: event.data.message }))
        return
      }
      resolve(new Float32Array(event.data.samples))
    }
    worker.postMessage({ buffer, expectedBytes: source.byteLength, sha256: source.sha256 }, [
      buffer,
    ])
  })
}
