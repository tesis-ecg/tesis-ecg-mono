/**
 * El canal entre el chaleco y el backend: qué le pasa a una trama entre que sale
 * de la SD y entra al POST.
 *
 * Está separado de la generación (`batchBuilder.ts`) porque son dos cosas
 * distintas y antes estaban juntas: las anomalías quedaban horneadas dentro del
 * lote generado, así que una trama descartada no existía más y no había forma de
 * retransmitirla. El equipo real graba una vez y transmite tantas veces como haga
 * falta hasta que el backend confirme (`INTEGRACION.md` §4.6).
 */

import { FRAME_BYTES, corruptCrc } from './frame'
import type { PendingFrame } from '../deviceClock'
import type { FrameAnomalies } from '../types'

/** PRNG determinista (mulberry32). No hace falta calidad criptográfica. */
export function makeRng(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export interface ChannelResult {
  body: Uint8Array
  /** `seq` que no salieron: el backend va a ver un hueco y cortar el ACK ahí. */
  droppedSeqs: number[]
  corruptedSeqs: number[]
  duplicatedSeqs: number[]
}

/**
 * Arma el cuerpo del POST a partir de la ventana de tramas sin confirmar.
 *
 * **Marca la ventana como intentada** (`attempts++`), y esa marca cambia el
 * comportamiento: `dropPct` y `corruptCrcPct` se sortean **solo en el primer
 * intento**. Una trama que ya se intentó una vez viaja intacta.
 *
 * Es una decisión de modelado, y es la que hace que el simulador sirva: la
 * pérdida que representan esos dos porcentajes es transitoria —corte de WiFi,
 * buffer del co-procesador lleno, trama que no llegó— y el reintento del
 * go-back-N pasa. Sorteando la pérdida en cada intento, con un 20 % el backlog
 * no drenaría nunca: la ventana avanza en promedio 4 tramas por request y una
 * hora de señal necesitaría miles de requests. El resultado sería el mismo
 * estudio congelado que había antes, pero con más código.
 *
 * `duplicatePct` sí se sortea en cada intento: un duplicado no impide el
 * progreso, el backend lo colapsa por `seq`.
 */
export function applyChannel(
  window: PendingFrame[],
  anomalies: FrameAnomalies,
  rng: () => number,
): ChannelResult {
  const out: Uint8Array[] = []
  const droppedSeqs: number[] = []
  const corruptedSeqs: number[] = []
  const duplicatedSeqs: number[] = []

  for (const frame of window) {
    const firstTry = frame.attempts === 0
    frame.attempts++

    if (firstTry && rng() * 100 < anomalies.dropPct) {
      droppedSeqs.push(frame.seq)
      continue
    }

    if (firstTry && rng() * 100 < anomalies.corruptCrcPct) {
      out.push(corruptCrc(frame.bytes))
      corruptedSeqs.push(frame.seq)
    } else {
      out.push(frame.bytes)
    }

    // Un duplicado exacto: normal y esperable cuando el equipo retransmite.
    if (rng() * 100 < anomalies.duplicatePct) {
      out.push(out[out.length - 1])
      duplicatedSeqs.push(frame.seq)
    }
  }

  if (anomalies.shuffle) {
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1))
      ;[out[i], out[j]] = [out[j], out[i]]
    }
  }

  const body = new Uint8Array(out.length * FRAME_BYTES)
  out.forEach((frame, i) => body.set(frame, i * FRAME_BYTES))

  return { body, droppedSeqs, corruptedSeqs, duplicatedSeqs }
}
