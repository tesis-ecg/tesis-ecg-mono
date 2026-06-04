import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'

import type { ECGSignal } from '../types'

interface EcgUrlResponse {
  url: string
  sampleRate: number
  startTimestamp: number
  durationMs: number
  sampleCount: number
}

export async function getStudyEcg(studyId: string): Promise<ECGSignal> {
  const { data: meta } = await api.get<EcgUrlResponse>(`/studies/${studyId}/ecg`)

  const response = await fetch(meta.url)
  if (!response.ok) {
    throw createApiError({
      status: response.status,
      code: 'UNKNOWN',
      message: 'No se pudo descargar el ECG del estudio.',
    })
  }

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
    samples: new Float32Array(buffer),
    startTimestamp: meta.startTimestamp,
  }
}
