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

interface EcgManifest {
  formatVersion: 1
  encoding: 'float32-le'
  sampleRate: number
  sampleCount: number
  startTimestamp: number
  durationMs: number
  raw: EcgObject
  levels: EcgLevel[]
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
  if (!level && source.byteLength > MAX_LEGACY_BYTES) {
    throw createApiError({
      status: 503,
      code: 'SERVER',
      message: 'El ECG todavía no tiene una vista optimizada disponible.',
    })
  }

  const response = await fetch(source.url, { signal })
  if (!response.ok) throwDownloadError(response.status)
  const buffer = await response.arrayBuffer()
  const samples = await decodeEcgObject(buffer, source, signal)
  const durationSec = Math.max(manifest.durationMs / 1000, 0.001)
  return {
    sampleRate: level ? samples.length / durationSec : manifest.sampleRate,
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
    samples: await decodeEcgObject(buffer, { byteLength: expectedBytes, sha256: null }, signal),
    startTimestamp: meta.startTimestamp,
  }
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
