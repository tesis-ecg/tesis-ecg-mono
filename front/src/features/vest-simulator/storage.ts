/**
 * Persistencia de la flota de chalecos simulados.
 *
 * Existe por un problema concreto: la API key en claro de un equipo **se
 * devuelve una sola vez**, al rotarla. Con la flota solo en memoria, recargar
 * la página la perdía —pero en la base ya había quedado rotada— y el chaleco
 * quedaba con una credencial muerta: 401 en cada envío, sin forma de recuperarla
 * más que volviendo a rotar.
 *
 * Guardar la key en `localStorage` es una decisión consciente y acotada:
 * `/__sim/vest` es admin-only, la key solo habilita a subir señal marcada como
 * dato simulado, y se revoca rotándola de nuevo.
 *
 * Se persisten `config` y el **reloj** del equipo (`seq`, `bootId`, uptime). Las
 * stats y el log son de la corrida: restaurarlos mostraría un "12 lotes
 * enviados" que no pasó en esta sesión.
 */

import type { DeviceClock } from './deviceClock'
import type { VestConfig } from './types'

const STORAGE_KEY = 'holter:vest-fleet'
const CLOCKS_KEY = 'holter:vest-clocks'

function isVestConfig(value: unknown): value is VestConfig {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.label === 'string' &&
    typeof candidate.serial === 'string' &&
    typeof candidate.apiKey === 'string' &&
    typeof candidate.signal === 'object' &&
    typeof candidate.frames === 'object' &&
    typeof candidate.network === 'object'
  )
}

/** Configs guardadas, o `[]` si no hay nada o lo que hay no se puede leer. */
export function loadFleet(): VestConfig[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Se filtra en vez de descartar todo: una config vieja e incompleta no
    // debería tirar abajo las que sí sirven. Los campos agregados después
    // toman su default acá y no en el consumidor, que si no tendría que
    // defenderse del `undefined` en cada lectura.
    return parsed.filter(isVestConfig).map((config) => ({
      ...config,
      placementOk: config.placementOk ?? true,
    }))
  } catch {
    // localStorage puede fallar entero (modo privado de Safari, cuota llena).
    // El simulador tiene que seguir andando sin persistencia.
    return []
  }
}

export function saveFleet(configs: VestConfig[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(configs))
  } catch {
    // Ídem: perder la persistencia no puede romper la corrida en curso.
  }
}

function isDeviceClock(value: unknown): value is DeviceClock {
  if (typeof value !== 'object' || value === null) return false
  const c = value as Record<string, unknown>
  return (
    typeof c.bootId === 'number' &&
    typeof c.nextSeq === 'number' &&
    typeof c.t0Ms === 'number' &&
    typeof c.uptimeMs === 'number' &&
    typeof c.batteryPct === 'number'
  )
}

/**
 * Relojes guardados, por `id` de chaleco.
 *
 * Se persisten por el mismo motivo que las keys: sin esto, un F5 devolvía el
 * equipo a `seq 0 / bootId 0`, un estado que el hardware no puede producir. El
 * backend lo leía como una retransmisión completa —y el estudio dejaba de
 * crecer— o, si el `bootId` del estudio era otro, aceptaba desde `seq 0` y
 * **sobreescribía** en S3 los segmentos ya archivados, porque se nombran con el
 * `first_seq` del lote.
 *
 * La SD (`pending`) no se guarda: son megabytes de binario y no entran en
 * `localStorage`. Recargar equivale a perder el backlog, y de ahí sale el botón
 * de "Reiniciar equipo" de la tarjeta.
 */
export function loadClocks(): Record<string, DeviceClock> {
  try {
    const raw = window.localStorage.getItem(CLOCKS_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(([, clock]) => isDeviceClock(clock)),
    ) as Record<string, DeviceClock>
  } catch {
    return {}
  }
}

export function saveClocks(clocks: Record<string, DeviceClock>): void {
  try {
    window.localStorage.setItem(CLOCKS_KEY, JSON.stringify(clocks))
  } catch {
    // Ídem.
  }
}
