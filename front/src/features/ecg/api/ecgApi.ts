import { api } from '@/lib/api'
import { createApiError, isApiError } from '@/lib/apiError'

import type { ECGSignal } from '../types'

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

interface EcgLevel extends EcgObject {
  samplesPerBucket: number
  pointCount: number
  encoding: 'minmax-float32-le'
}

interface EcgSegment extends EcgObject {
  startSampleIndex: number
  sampleCount: number
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
  formatVersion: 1 | 2
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
  const source = level ?? manifest.raw
  const segments = [...(manifest.segments ?? [])].sort(
    (a, b) => a.startSampleIndex - b.startSampleIndex,
  )
  if (!source && segments.length === 0) {
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

  const samples = source
    ? await downloadEcgObject(source, signal)
    : await downloadSegments(segments, signal)
  return {
    sampleRate: manifest.sampleRate,
    durationMs: recordingDurationMs(manifest.sampleCount, manifest.sampleRate),
    samples,
    startTimestamp: manifest.startTimestamp,
  }
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
  }
}

export function recordingDurationMs(sampleCount: number, sampleRate: number): number {
  return sampleRate > 0 ? (sampleCount / sampleRate) * 1000 : 0
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
