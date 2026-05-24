/**
 * Simula latencia de red para que los estados de loading/skeleton sean visibles
 * en el dashboard mientras el backend no exista.
 *
 * Cuando se conecten los endpoints reales (ver TES-16/17/18/19), borrar este
 * archivo y eliminar las llamadas a `mockDelay()` en cada `features/.../api.ts`.
 */
export function mockDelay(ms = randomBetween(300, 700)): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function randomBetween(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}
