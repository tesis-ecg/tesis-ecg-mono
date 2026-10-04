/**
 * Generación de un lote: señal → tramas comprimidas.
 *
 * Módulo puro, sin nada del entorno de worker, para que se pueda testear y usar
 * como fallback en el hilo principal. La plomería del worker vive en
 * `vestWorker.ts`.
 *
 * Acá **no** se aplican anomalías de transmisión. Lo que sale es lo que el equipo
 * graba en la flash; lo que le pasa después en el aire es cosa de `channel.ts`.
 * La separación es lo que permite retransmitir: una trama descartada sigue
 * existiendo.
 */

import { FRAME_BYTES } from './frame'
import { encodeSamples } from './riceEncoder'
import {
  generateEcg,
  type GeneratorState,
  type ResolvedEpisode,
  type SignalProfile,
} from './signal'

export interface VestWorkerRequest {
  requestId: number
  profile: SignalProfile
  durationSec: number
  episodes: ResolvedEpisode[]
  /** Estado del generador al terminar el lote anterior: la señal continúa. */
  genState: GeneratorState
  firstSeq: number
  bootId: number
  /** `millis()` del equipo en la primera muestra. */
  t0Ms: number
  /** Hora de pared de la primera muestra. */
  wallStartEpochMs: number
  /** `hdrFlags` bit 3. Va en la cabecera, así que se define al grabar. */
  simulated: boolean
}

export interface VestWorkerResponse {
  requestId: number
  /** Tramas limpias concatenadas, en orden de `seq`. */
  body: ArrayBuffer
  framesGenerated: number
  lastSeq: number
  uncompressedBytes: number
  sampleCount: number
  beats: number
  genState: GeneratorState
  /** 0 bien, 1 electrodo suelto, 2 calidad mala; uno por segundo de señal. */
  secondStatus: Uint8Array
  leadFlags: number
  worstSqi: number
}

export function buildBatch(request: VestWorkerRequest): VestWorkerResponse {
  const generated = generateEcg({
    profile: request.profile,
    durationSec: request.durationSec,
    episodes: request.episodes,
    state: request.genState,
    startT0Ms: request.t0Ms,
    wallStartEpochMs: request.wallStartEpochMs,
  })
  const encoded = encodeSamples(generated.samples, {
    firstSeq: request.firstSeq,
    bootId: request.bootId,
    simulated: request.simulated,
  })

  const body = new Uint8Array(encoded.length * FRAME_BYTES)
  encoded.forEach((frame, i) => body.set(frame, i * FRAME_BYTES))

  return {
    requestId: request.requestId,
    body: body.buffer,
    framesGenerated: encoded.length,
    lastSeq: request.firstSeq + encoded.length - 1,
    uncompressedBytes: generated.samples.length * 4,
    sampleCount: generated.samples.length,
    beats: generated.beats,
    genState: generated.state,
    secondStatus: generated.secondStatus,
    leadFlags: generated.leadFlags,
    worstSqi: generated.worstSqi,
  }
}

/** Parte el cuerpo devuelto por el worker en tramas de 256 B. */
export function splitFrames(body: ArrayBuffer): Uint8Array[] {
  const bytes = new Uint8Array(body)
  const frames: Uint8Array[] = []
  for (let offset = 0; offset < bytes.length; offset += FRAME_BYTES) {
    frames.push(bytes.subarray(offset, offset + FRAME_BYTES))
  }
  return frames
}
