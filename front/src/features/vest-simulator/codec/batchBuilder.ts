/**
 * Generación de un lote: señal → tramas comprimidas.
 *
 * Módulo puro, sin nada del entorno de worker, para que se pueda testear y usar
 * como fallback en el hilo principal. La plomería del worker vive en
 * `vestWorker.ts`.
 *
 * Acá **no** se aplican anomalías de transmisión. Lo que sale es lo que el equipo
 * graba en la SD; lo que le pasa después en el aire es cosa de `channel.ts`. La
 * separación es lo que permite retransmitir: una trama descartada sigue existiendo.
 */

import { FRAME_BYTES } from './frame'
import { encodeSamples } from './riceEncoder'
import { generateSignal, type SignalConfig } from './signal'

export interface VestWorkerRequest {
  requestId: number
  signal: SignalConfig
  firstSeq: number
  bootId: number
  t0Ms: number
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
}

export function buildBatch(request: VestWorkerRequest): VestWorkerResponse {
  const { samples, beats } = generateSignal(request.signal, request.t0Ms)
  const encoded = encodeSamples(samples, {
    nChannels: request.signal.nChannels,
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
    uncompressedBytes: samples.length * 4 * request.signal.nChannels,
    sampleCount: samples.length,
    beats,
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
