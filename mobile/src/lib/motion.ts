import { useEffect } from 'react'
import {
  cancelAnimation,
  Easing,
  FadeInDown,
  ReduceMotion,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated'

/**
 * Movimiento de la app.
 *
 * Dos reglas, y las dos vienen de quién la usa. Nada dura más de 260 ms: una
 * animación que se hace esperar es una animación que estorba cuando el paciente
 * abrió la app para responder un aviso. Y nada se mueve sin motivo: el
 * movimiento explica de dónde salió algo, no adorna.
 *
 * `ReduceMotion.System` es el default de Reanimated, pero se declara explícito
 * para que quede escrito que "Reducir movimiento" de los ajustes del sistema
 * apaga esto — el usuario objetivo tiene más chances que el promedio de tenerlo
 * activado, sea por mareo o por preferencia.
 */

/** Duración base. Corta a propósito. */
export const DURATION = 260

/** Separación entre elementos de una misma lista al entrar. */
export const STAGGER = 45

/** Cuántos elementos se escalonan antes de que todos entren juntos. */
const MAX_STAGGERED = 6

/**
 * Entrada de un elemento en una lista, escalonada según su posición.
 *
 * El tope existe porque en un historial de treinta registros, escalonarlos
 * todos deja al último entrando casi un segundo y medio después del primero.
 */
export function enterAt(index: number) {
  const step = Math.min(index, MAX_STAGGERED)
  return FadeInDown.duration(DURATION)
    .delay(step * STAGGER)
    .reduceMotion(ReduceMotion.System)
}

/**
 * Hundimiento al tocar una superficie.
 *
 * Devuelve el estilo animado y los dos handlers que lo disparan. Está acá y no
 * copiado en cada pantalla porque el valor del hundimiento tiene que ser el
 * mismo en toda la app: si cada card se hunde distinto, el gesto deja de
 * sentirse como una sola cosa.
 */
export function usePressScale(scale = 0.97) {
  const reduceMotion = useReducedMotion()
  const pressed = useSharedValue(0)

  const style = useAnimatedStyle(() => ({
    transform: [{ scale: reduceMotion ? 1 : 1 - pressed.value * (1 - scale) }],
  }))

  return {
    style,
    onPressIn: () => {
      pressed.value = withTiming(1, { duration: 90 })
    },
    onPressOut: () => {
      pressed.value = withTiming(0, { duration: 160 })
    },
  }
}

/** Cuánto tarda una vuelta del ícono que gira mientras algo carga. */
export const SPIN_DURATION = 900

/**
 * Giro continuo mientras `active`, para el ícono de un botón que está cargando.
 *
 * Es la única animación de la app que **no** respeta "Reducir movimiento", y
 * vale la pena decir por qué: acá el movimiento no explica de dónde salió algo
 * ni adorna, es el único indicio de que el toque entró y todavía no terminó.
 * Apagarlo deja al botón sin ninguna respuesta, que es peor que el mareo que la
 * preferencia quiere evitar — y es el mismo criterio del sistema operativo, que
 * sigue girando su propio indicador de carga con la opción activada.
 *
 * Al frenar no vuelve para atrás: termina la vuelta en curso y recién ahí se
 * resetea. Animar de 300° a 0° se lee como un rebobinado, o sea como que algo
 * se deshizo, que es lo contrario de lo que acaba de pasar.
 */
export function useSpin(active: boolean, duration = SPIN_DURATION) {
  const angle = useSharedValue(0)

  useEffect(() => {
    if (!active) {
      cancelAnimation(angle)
      const lastTurn = Math.ceil(angle.value / 360) * 360
      angle.value = withTiming(lastTurn, { duration: DURATION }, (finished) => {
        if (finished) angle.value = 0
      })
      return
    }
    angle.value = 0
    angle.value = withRepeat(withTiming(360, { duration, easing: Easing.linear }), -1, false)
    // Igual que en `usePulse`: sin esto el loop se sigue calculando en el hilo
    // de UI después de que la pantalla se desmontó.
    return () => cancelAnimation(angle)
  }, [active, duration, angle])

  return useAnimatedStyle(() => ({ transform: [{ rotate: `${angle.value}deg` }] }))
}

/**
 * Duración de un ciclo del latido de "en vivo".
 *
 * Es la única animación de la app que dura más de `DURATION`, y es a propósito:
 * un pulso corto se lee como una alarma parpadeando, que es justo lo contrario
 * de lo que dice el estado "Grabando". A dos segundos por ciclo el movimiento
 * se lee como una respiración —está vivo, no está pidiendo nada— y a esa
 * velocidad tampoco compite con el contenido que el paciente vino a leer.
 */
export const PULSE_DURATION = 2000

/**
 * Progreso 0→1 que se repite para siempre, para los indicadores de "en vivo".
 *
 * Devuelve el valor y no un estilo: cada indicador interpola distinto (el halo
 * crece y se desvanece, el punto respira) y las dos cosas tienen que ir en fase
 * o se leen como dos animaciones sueltas encima del mismo punto.
 *
 * Con "Reducir movimiento" activado se queda en 0 y no arranca nunca: no
 * alcanza con `reduceMotion` en el `withTiming`, porque un loop infinito con
 * la animación deshabilitada igual salta al valor final en cada vuelta.
 */
export function usePulse(duration = PULSE_DURATION) {
  const reduceMotion = useReducedMotion()
  const progress = useSharedValue(0)

  useEffect(() => {
    if (reduceMotion) {
      progress.value = 0
      return
    }
    progress.value = 0
    progress.value = withRepeat(
      withTiming(1, { duration, easing: Easing.out(Easing.quad) }),
      -1,
      false,
    )
    // Sin esto el loop sigue corriendo en el hilo de UI después de que la
    // pantalla se desmontó: nadie lo dibuja, pero se sigue calculando.
    return () => cancelAnimation(progress)
  }, [duration, progress, reduceMotion])

  return progress
}
