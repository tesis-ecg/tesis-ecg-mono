import { useEffect } from 'react'
import {
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated'

import { VEST_AURA_TONES, vestAura, type VestAuraTone } from '@/lib/gradients'
import { DURATION, usePulse } from '@/lib/motion'
import { AnimatedView, View } from '@/tw'

/**
 * Diámetro del halo.
 *
 * Fijo, en la misma unidad que `VEST_HEIGHT`, y no un porcentaje del ancho: la
 * foto se escala por alto, así que el chaleco mide lo mismo en un iPhone SE que
 * en un Max y un aura proporcional al ancho crecería sólo en el equipo grande,
 * donde ya sobra aire.
 *
 * 280 y no más porque ese es el ancho útil de la pantalla más angosta que
 * soportamos (un SE son 320 pt menos los 20 de margen de cada lado). Por arriba
 * de eso el halo se saldría de la columna, y aunque en el borde ya es
 * transparente, Android recorta a los hijos que se pasan del padre.
 */
const AURA_SIZE = 280

/** Cuánto se ensancha el halo en el pico de la respiración. */
const BREATH_SCALE = 0.06

/** Cuánto se apaga el halo en el valle de la respiración. */
const BREATH_DIM = 0.18

/**
 * El halo de color que se apoya detrás de la foto del chaleco.
 *
 * Dice de un vistazo lo mismo que la pastilla, pero sin leer: el paciente abre
 * Dispositivo para confirmar que el equipo está andando, y el color del fondo
 * ya contestó antes de que llegue al texto. Va detrás de la prenda y no
 * alrededor de un ícono para que se lea como luz sobre el objeto real —lo que
 * asoma es el derrame a los costados del chaleco, no un círculo dibujado.
 *
 * **Grabando respira.** No parpadea ni se expande en ondas: eso ya lo hace el
 * punto de la pastilla, que está apoyado sobre esta misma foto, y repetir el
 * gesto dos veces en el mismo cuadro lo convierte en ruido. El halo hace lo
 * complementario —crece y se ilumina despacio, como una respiración— al mismo
 * compás (`PULSE_DURATION`) para que la pantalla tenga un solo ritmo y no dos.
 *
 * El estado no salta de un color a otro: las cuatro capas están montadas
 * siempre y lo que se cruza son las opacidades. Un halo de 280 pt cambiando de
 * verde a rojo de un frame para el otro se lee como un parpadeo de la pantalla,
 * no como una noticia sobre el chaleco.
 */
export function VestAura({
  tone,
  /** Grabando: el halo respira. */
  live,
}: {
  tone: VestAuraTone
  live: boolean
}) {
  const reduceMotion = useReducedMotion()
  const progress = usePulse()

  /*
    Cuánto de la respiración se aplica, de 0 a 1. Es un valor aparte y no un
    `if` sobre `live` para que el efecto **entre y salga** con el color en vez
    de aparecer y desaparecer de golpe: cuando el chaleco arranca a grabar, el
    halo se pone verde y recién ahí empieza a respirar.

    `usePulse` sigue girando aunque no haya nada vivo. Es un solo loop en el
    hilo de UI, y frenarlo dejaría la respiración congelada a mitad de camino
    justo mientras se está apagando.
  */
  const liveness = useSharedValue(live && !reduceMotion ? 1 : 0)

  useEffect(() => {
    liveness.value = withTiming(live && !reduceMotion ? 1 : 0, { duration: DURATION })
  }, [live, reduceMotion, liveness])

  const breath = useAnimatedStyle(() => {
    /*
      El progreso de `usePulse` es una rampa de 0 a 1 con `ease-out`; llevarlo a
      un ida y vuelta le deja el pico a un tercio del ciclo. O sea: inspira
      rápido y exhala largo, que es como respira algo vivo. Simétrico se leía
      como un latido de máquina.
    */
    const wave = interpolate(progress.value, [0, 0.5, 1], [0, 1, 0])

    return {
      transform: [{ scale: 1 + liveness.value * wave * BREATH_SCALE }],
      // Se atenúa en el valle en vez de encenderse en el pico: la opacidad no
      // puede pasar de 1, así que el brillo máximo tiene que ser el reposo.
      opacity: 1 - liveness.value * BREATH_DIM * (1 - wave),
    }
  })

  return (
    <View pointerEvents="none" className="absolute inset-0 items-center justify-center">
      <AnimatedView style={[{ width: AURA_SIZE, height: AURA_SIZE }, breath]}>
        {VEST_AURA_TONES.map((auraTone) => (
          <AuraLayer key={auraTone} tone={auraTone} active={auraTone === tone} />
        ))}
      </AnimatedView>
    </View>
  )
}

/**
 * Una de las capas de color, encendida o apagada.
 *
 * El gradiente va por `style` y la opacidad también, en el mismo array: son dos
 * propiedades distintas, así que se componen en vez de pisarse. Lo que no se
 * puede es animar el gradiente en sí —`experimental_backgroundImage` es un
 * string que React Native parsea, no un valor interpolable—, y de ahí que el
 * cambio de estado se resuelva cruzando capas.
 */
function AuraLayer({ tone, active }: { tone: VestAuraTone; active: boolean }) {
  const opacity = useSharedValue(active ? 1 : 0)

  useEffect(() => {
    opacity.value = withTiming(active ? 1 : 0, { duration: DURATION })
  }, [active, opacity])

  const fade = useAnimatedStyle(() => ({ opacity: opacity.value }))

  return <AnimatedView className="absolute inset-0" style={[vestAura[tone], fade]} />
}
