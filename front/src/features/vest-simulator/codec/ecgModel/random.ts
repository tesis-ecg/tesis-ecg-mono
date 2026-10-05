/**
 * PRNG del generador de ECG con el estado **expuesto**.
 *
 * Es el mismo mulberry32 de `channel.ts`, pero el generador necesita guardar
 * el estado entre lotes: es lo que hace que el lote siguiente continúe la
 * misma señal en vez de repetir la anterior desde la misma semilla.
 */
export class Rng {
  state: number

  constructor(state: number) {
    this.state = state >>> 0
  }

  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0
    let t = this.state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  /** Normal estándar (Box-Muller, sin cachear el segundo valor). */
  normal(): number {
    const u = Math.max(this.next(), 1e-12)
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * this.next())
  }
}

/**
 * Un paso de Ornstein-Uhlenbeck: ruido con memoria `tauSec` y desvío
 * estacionario `std`. Es el modelo de todo lo que deriva sin irse al infinito:
 * línea de base, offset de continua, amplitud de la red.
 */
export function ouStep(x: number, tauSec: number, std: number, dtSec: number, rng: Rng): number {
  return x - (x / tauSec) * dtSec + std * Math.sqrt((2 * dtSec) / tauSec) * rng.normal()
}
