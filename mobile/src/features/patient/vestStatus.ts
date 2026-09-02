/**
 * Cuándo el chaleco cuenta como mal colocado.
 *
 * Aparte de `deviceMeta` a propósito: ese módulo importa los íconos de
 * `lucide-react-native` y eso lo deja fuera del alcance de Vitest, que en este
 * proyecto corre sobre lógica pura y sin transformar dependencias nativas.
 *
 * Antes esto era una heurística sobre los avisos: cualquier alerta de chaleco de
 * la última hora contaba como "ahora". Era lo único posible mientras el backend
 * no guardaba si el episodio se había cerrado —`signal_recovered` archivaba la
 * telemetría y no dejaba huella—, y tenía el defecto obvio: el paciente se
 * acomodaba el equipo y el cartel rojo seguía puesto media hora más. Ahora el
 * estado viene del propio equipo (`device.placement_ok`, escrito por
 * `POST /ingest/device-status`) y no hay nada que inferir.
 */

import type { DeviceStatus } from './types'

/**
 * Si el chaleco está mal colocado ahora mismo.
 *
 * Solo `bad` pinta el cartel. Sin dispositivo, sin dato cargado todavía o con
 * un equipo que nunca reportó, la pantalla no afirma nada: un rojo por falta de
 * información manda al paciente a acomodarse un chaleco que puede estar
 * perfecto, y repetido unas cuantas veces deja de leerse.
 */
export function isVestMisplaced(device: DeviceStatus | undefined | null): boolean {
  return device?.hasDevice === true && device.vestPlacement === 'bad'
}
