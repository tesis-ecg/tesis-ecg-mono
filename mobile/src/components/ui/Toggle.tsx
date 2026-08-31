import { useEffect } from 'react'
import {
  Easing,
  interpolate,
  interpolateColor,
  ReduceMotion,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated'

import { AnimatedView, Pressable } from '@/tw'
import * as haptics from '@/lib/haptics'

/**
 * Medidas del interruptor.
 *
 * Es más grande que el nativo de iOS (51×31) a propósito, como el resto de los
 * controles de la app: el blanco de toque real lo dan el `hitSlop` y estos
 * números juntos, y el usuario objetivo puede tener menos precisión motriz.
 */
const TRACK_WIDTH = 56
const TRACK_HEIGHT = 34
const KNOB = 28
const PADDING = 3
const TRAVEL = TRACK_WIDTH - KNOB - PADDING * 2

/**
 * Los dos colores del riel.
 *
 * Literales porque `interpolateColor` interpola entre dos colores resueltos y
 * no puede leer una clase de Tailwind. Son `--color-gray-200` y
 * `--color-primary-500` de `global.css`.
 */
const TRACK_OFF = '#cbcfd3'
const TRACK_ON = '#0b2185'

interface ToggleProps {
  value: boolean
  onPress: () => void
  accessibilityLabel: string
  disabled?: boolean
}

/**
 * Interruptor de la app.
 *
 * Propio y no el `Switch` de React Native, por la misma regla que el resto de
 * `ui/`: el de RN se dibuja distinto en cada plataforma —verde y redondo en
 * iOS, del color de acento en Android— y el requisito del producto es que las
 * dos se vean igual.
 *
 * **Es controlado y no guarda estado propio.** El único que usa hoy refleja un
 * permiso del sistema operativo, que se puede cambiar desde afuera de la app:
 * si el interruptor se prendiera solo al tocarlo, un permiso rechazado dejaría
 * la palanca encendida mintiendo. Acá se mueve cuando cambia `value`, nunca por
 * el toque.
 */
export function Toggle({ value, onPress, accessibilityLabel, disabled = false }: ToggleProps) {
  const progress = useSharedValue(value ? 1 : 0)

  useEffect(() => {
    progress.value = withTiming(value ? 1 : 0, {
      duration: 220,
      easing: Easing.out(Easing.cubic),
      reduceMotion: ReduceMotion.System,
    })
  }, [value, progress])

  const track = useAnimatedStyle(() => ({
    backgroundColor: interpolateColor(progress.value, [0, 1], [TRACK_OFF, TRACK_ON]),
  }))

  const knob = useAnimatedStyle(() => ({
    transform: [{ translateX: interpolate(progress.value, [0, 1], [0, TRAVEL]) }],
  }))

  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ checked: value, disabled }}
      disabled={disabled}
      // El riel mide 34 de alto: el hitSlop lo lleva a los 44 pt mínimos.
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      onPress={() => {
        haptics.selection()
        onPress()
      }}
      className={disabled ? 'opacity-40' : undefined}
    >
      {/* El `Pressable` escucha y estas dos `View` se pintan y se animan. */}
      <AnimatedView
        style={[
          {
            width: TRACK_WIDTH,
            height: TRACK_HEIGHT,
            borderRadius: TRACK_HEIGHT / 2,
            padding: PADDING,
          },
          track,
        ]}
      >
        <AnimatedView
          className="shadow-[0px_1px_3px_rgba(23,45,57,0.28)]"
          style={[
            { width: KNOB, height: KNOB, borderRadius: KNOB / 2, backgroundColor: '#ffffff' },
            knob,
          ]}
        />
      </AnimatedView>
    </Pressable>
  )
}
