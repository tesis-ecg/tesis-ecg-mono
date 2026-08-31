/**
 * Cuándo el chaleco cuenta como mal colocado.
 *
 * Aparte de `deviceMeta` a propósito: ese módulo importa los íconos de
 * `lucide-react-native` y eso lo deja fuera del alcance de Vitest, que en este
 * proyecto corre sobre lógica pura y sin transformar dependencias nativas. Acá
 * no hay más que fechas, así que la regla se puede testear.
 */

import type { PatientAlert } from './types'

/** El aviso que manda el propio chaleco cuando pierde contacto con la piel. */
export const VEST_MISPLACED_KIND = 'vest_misplaced'

/**
 * Cuánto tiempo un aviso de chaleco mal colocado se sigue leyendo como "ahora".
 *
 * El backend no guarda si el episodio se cerró: `signal_recovered` archiva la
 * telemetría y no crea nada (`ingest_service.report_device_status`), así que de
 * este lado no hay un "ya está bien" que apagar. Lo que sí hay es que mientras
 * el chaleco siga mal puesto vuelve a avisar cada
 * `vest_status_debounce_minutes` —30 por default—, y el doble de esa ventana es
 * el margen que aguanta un reporte perdido sin dejar el cartel rojo colgado
 * media mañana después de que el paciente ya se acomodó el equipo.
 */
export const VEST_ALERT_WINDOW_MS = 60 * 60 * 1000

/**
 * Si el chaleco está mal colocado ahora mismo.
 *
 * Recorre todos los avisos en vez de mirar sólo el primero: la lista viene
 * ordenada por `created_at` y el que importa acá es `detectedAt`, que para los
 * avisos cargados de golpe —los de la seed de desarrollo— no van en el mismo
 * orden.
 *
 * `now` se inyecta para poder testear la ventana sin tocar el reloj.
 */
export function isVestMisplaced(alerts: PatientAlert[], now: number = Date.now()): boolean {
  return alerts.some((alert) => {
    if (alert.kind !== VEST_MISPLACED_KIND) return false
    const detected = new Date(alert.detectedAt).getTime()
    return !Number.isNaN(detected) && now - detected < VEST_ALERT_WINDOW_MS
  })
}
