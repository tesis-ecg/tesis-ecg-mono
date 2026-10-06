import { api } from '@/lib/api'
import { createApiError, isApiError } from '@/lib/apiError'

import type { ECGAnnotationCategory, ECGAnnotationSeverity, ECGSignal } from '../types'
import { createEcgDetailSource, type DetailPiece } from './ecgDetail'

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
  anchorMatchesBoot?: boolean | null
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
  startTimeVerified?: boolean
  durationMs: number
  status?: string
  isSimulated?: boolean
  viewKind?: 'raw' | 'filtered_visualization'
  raw: EcgObject | null
  levels: EcgLevel[]
  segments?: EcgSegment[]
  timeline?: EcgTimelineSegment[]
  annotations?: EcgAnnotation[]
}

export interface EcgReportWindowRequest {
  id: string
  startEpochMs: number
  endEpochMs: number
}

export interface EcgReportWindow {
  id: string
  startEpochMs: number
  endEpochMs: number
  timestampsMs: number[]
  samplesMv: number[]
  gapIndices: number[]
  source: 'raw' | 'envelope' | 'filtered_visualization'
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
    manifest.sampleRate,
    manifest.startTimestamp,
    timeline,
    level?.samplesPerBucket ?? null,
  )
  const startTimestamp = timestampsMs.length > 0 ? timestampsMs[0] : manifest.startTimestamp
  const processedSampleCount = level
    ? Math.min(manifest.sampleCount, (samples.length / 2) * level.samplesPerBucket)
    : samples.length
  const recordedEndMs =
    timeline.length > 0
      ? timeline[timeline.length - 1].endEpochMs
      : startTimestamp + recordingDurationMs(manifest.sampleCount, manifest.sampleRate)
  // El eje termina donde termina la señal procesada. El tramo que el backend
  // todavía no filtró no se muestra: aparece solo cuando llega, en vez de
  // ocupar el final del eje con una franja vacía.
  const endMs =
    processedSampleCount < manifest.sampleCount
      ? Math.max(
          sampleToEpochMs(
            processedSampleCount,
            manifest.sampleRate,
            manifest.startTimestamp,
            timeline,
          ),
          // buildTimestamps aplana las anclas que retroceden entre tramos. El
          // eje no puede terminar antes del último punto ya dibujado.
          timestampsMs[timestampsMs.length - 1] ?? startTimestamp,
        )
      : recordedEndMs

  return {
    sampleRate: manifest.sampleRate,
    durationMs: endMs - startTimestamp,
    samples,
    startTimestamp,
    timestampsMs,
    gapIndices,
    timeline,
    // Solo hace falta cuando lo descargado es un resumen.
    detail: level ? createDetail(studyId, manifest, timeline) : undefined,
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
    metadata: {
      formatVersion: manifest.formatVersion,
      encoding: manifest.encoding,
      sampleCount: manifest.sampleCount,
      isSimulated: Boolean(manifest.isSimulated),
      overviewSamplesPerBucket: level?.samplesPerBucket ?? null,
      processedSampleCount,
      startTimeVerified:
        (manifest.startTimeVerified ?? true) &&
        timeline.every((segment) => segment.anchorMatchesBoot === true),
      viewKind: manifest.viewKind ?? 'raw',
    },
  }
}

/**
 * Las piezas de las que el visor saca las muestras de un tramo, siempre de la
 * misma vista que el resumen: los segmentos del manifest ya son los de esa vista
 * (los filtrados si el estudio tiene vista filtrada). Un estudio sin segmentos
 * (los seedeados) tiene el crudo en un solo objeto: sirve si el resumen también
 * es crudo y si es chico, porque se baja entero.
 */
function detailPieces(manifest: EcgManifest): DetailPiece[] {
  const segments = manifest.segments ?? []
  if (segments.length > 0) {
    return segments.map((segment) => ({
      url: segment.url,
      expiresAt: segment.expiresAt,
      byteLength: segment.byteLength,
      sha256: segment.sha256,
      startSampleIndex: segment.startSampleIndex,
      sampleCount: segment.sampleCount,
    }))
  }
  const raw = manifest.raw
  if (raw && manifest.viewKind !== 'filtered_visualization' && raw.byteLength <= MAX_LEGACY_BYTES) {
    return [{ ...raw, startSampleIndex: 0, sampleCount: Math.floor(raw.byteLength / 4) }]
  }
  return []
}

function createDetail(
  studyId: string,
  manifest: EcgManifest,
  timeline: EcgTimelineSegment[],
): ECGSignal['detail'] {
  const pieces = detailPieces(manifest)
  if (pieces.length === 0) return undefined
  return createEcgDetailSource({
    studyId,
    pieces,
    download: (piece, signal) => downloadEcgObject(piece, signal),
    refresh: async () => {
      const { data } = await api.get<EcgManifest>(`/studies/${studyId}/ecg/manifest`)
      return detailPieces(data)
    },
    // La misma línea de tiempo que el resumen que está en pantalla, aunque el
    // manifest renovado traiga otra: una muestra tiene que caer donde el
    // resumen dibujó su balde.
    timestamps: (firstSample, count) =>
      buildTimestamps(
        count,
        manifest.sampleRate,
        manifest.startTimestamp,
        timeline,
        null,
        firstSample,
      ),
  })
}

export async function getStudyEcgReportWindows(
  studyId: string,
  windows: EcgReportWindowRequest[],
  signal?: AbortSignal,
): Promise<EcgReportWindow[]> {
  const { data } = await api.post<{ windows: EcgReportWindow[] }>(
    `/studies/${studyId}/ecg/report-windows`,
    { windows },
    { signal },
  )
  return data.windows
}

/**
 * Hora de pared de cada punto dibujado, y dónde se corta la traza.
 *
 * El array descargado puede ser la señal cruda o un nivel de la pirámide, así
 * que un punto no es necesariamente una muestra: cada par min/max ocupa un
 * bucket fijo. El índice de ese bucket se traduce a hora con la línea de tiempo.
 */
function buildTimestamps(
  pointCount: number,
  sampleRate: number,
  fallbackStartMs: number,
  timeline: EcgTimelineSegment[],
  samplesPerBucket: number | null,
  firstPoint = 0,
): { timestampsMs: Float64Array; gapIndices: number[] } {
  const timestampsMs = new Float64Array(pointCount)
  const gapIndices: number[] = []
  if (pointCount === 0) return { timestampsMs, gapIndices }

  const samplesPerPoint = samplesPerBucket === null ? 1 : samplesPerBucket / 2
  if (timeline.length === 0 || sampleRate <= 0) {
    const dt = sampleRate > 0 ? (samplesPerPoint * 1000) / sampleRate : 0
    for (let i = 0; i < pointCount; i++) timestampsMs[i] = fallbackStartMs + (firstPoint + i) * dt
    return { timestampsMs, gapIndices }
  }

  let cursor = 0
  // Un tramo que no arranca en el primer punto no corta en su primer punto: el
  // corte con lo anterior es asunto de quien lo empalma.
  let previousOrdinal: number | null = firstPoint === 0 ? timeline[0].ordinal : null
  let previousMs = -Infinity
  for (let i = 0; i < pointCount; i++) {
    const sample = (firstPoint + i) * samplesPerPoint
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
    previousMs = Math.max(
      segment.startEpochMs +
        (within * (segment.endEpochMs - segment.startEpochMs)) / Math.max(segment.sampleCount, 1),
      previousMs,
    )
    timestampsMs[i] = previousMs
    if (segment.ordinal !== previousOrdinal) {
      if (previousOrdinal !== null) gapIndices.push(i)
      previousOrdinal = segment.ordinal
    }
  }
  return { timestampsMs, gapIndices }
}

/**
 * Hora de pared de un índice de muestra, con la misma cuenta que
 * `buildTimestamps`: el tramo que lo contiene reparte su duración de pared
 * entre sus muestras.
 */
function sampleToEpochMs(
  sample: number,
  sampleRate: number,
  fallbackStartMs: number,
  timeline: EcgTimelineSegment[],
): number {
  if (timeline.length === 0 || sampleRate <= 0) {
    return fallbackStartMs + (sampleRate > 0 ? (sample * 1000) / sampleRate : 0)
  }
  let segment = timeline[0]
  for (const candidate of timeline) {
    if (candidate.startSampleIndex > sample) break
    segment = candidate
  }
  const within = Math.min(Math.max(sample - segment.startSampleIndex, 0), segment.sampleCount)
  return (
    segment.startEpochMs +
    (within * (segment.endEpochMs - segment.startEpochMs)) / Math.max(segment.sampleCount, 1)
  )
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
    metadata: {
      formatVersion: 1,
      encoding: 'float32-le',
      sampleCount: meta.sampleCount,
      isSimulated: false,
      overviewSamplesPerBucket: null,
    },
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
