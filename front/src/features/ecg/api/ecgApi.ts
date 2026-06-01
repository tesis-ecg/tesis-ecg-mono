// import { api } from '@/lib/api'
import { mockDelay } from '@/lib/mockDelay'

import { mockEcgSignal } from '../mocks'
import type { ECGSignal } from '../types'

/**
 * GET /studies/:id/ecg — Señal ECG cruda del estudio.
 *
 * BACKEND PENDIENTE — ver TES-32. El payload real será probablemente binario
 * (`application/octet-stream` + Float32Array LE) o un pre-signed S3 URL. NO va
 * a venir como JSON con array de floats — sería >5 MB y el parsing en cliente
 * mata la performance del primer render.
 *
 * Cuando esté el endpoint real:
 *   1. Borrar el bloque `// MOCK ↓ ... // MOCK ↑`.
 *   2. Reemplazar por `await api.get(...)` con `responseType: 'arraybuffer'`
 *      y un wrapper que decodee el header + crea el Float32Array.
 */
export async function getStudyEcg(studyId: string): Promise<ECGSignal> {
  // TODO(TES-32 backend): cuando exista, fetch binario.
  // const { data } = await api.get<ArrayBuffer>(`/studies/${studyId}/ecg`, {
  //   responseType: 'arraybuffer',
  // })
  // return decodeEcgPayload(data)

  // MOCK ↓ — `mockDelay` corto: la generación de 900k samples ya suma latencia.
  await mockDelay(150)
  return mockEcgSignal(studyId)
  // MOCK ↑
}
