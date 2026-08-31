import * as Haptics from 'expo-haptics'
import { Platform } from 'react-native'

/**
 * Respuesta táctil de la app.
 *
 * Centralizado para no repetir el guard de plataforma en cada llamada: en web
 * `expo-haptics` no hace nada pero igual devuelve una promesa, y esparcir el
 * `Platform.OS !== 'web'` por las pantallas es cómo se termina olvidando uno.
 *
 * El criterio de cuándo vibrar: cada vez que el toque cambia algo que el
 * paciente no necesariamente ve. Un chip que se marca, una pestaña que cambia,
 * un formulario que salió. El usuario objetivo puede tener el teléfono en el
 * bolsillo o la pantalla a contraluz, y el golpecito es lo que le confirma que
 * el toque entró. Nunca se usa como decoración: si algo vibra sin haber pasado
 * nada, deja de significar.
 */

function run(effect: () => Promise<void>): void {
  if (Platform.OS === 'web') return
  // Las vibraciones nunca pueden romper la acción que las acompaña: si el
  // motor háptico no está disponible, el toque igual tiene que funcionar.
  void effect().catch(() => {})
}

/** Selección: cambiar de pestaña, marcar un chip, elegir un filtro. */
export function selection(): void {
  run(() => Haptics.selectionAsync())
}

/** Toque sobre una superficie: abrir una card, tocar un aviso. */
export function tap(): void {
  run(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light))
}

/** El registro se envió. Es el único "éxito" de la app, y se siente distinto. */
export function success(): void {
  run(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success))
}

/** Falta completar algo del formulario. */
export function warning(): void {
  run(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning))
}

/** No se pudo: credenciales mal, o el envío falló. */
export function error(): void {
  run(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error))
}
