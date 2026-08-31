import { interpolate, useAnimatedStyle } from 'react-native-reanimated'

import { AnimatedView, Text, View } from '@/tw'
import { cn } from '@/lib/cn'
import { usePulse } from '@/lib/motion'

type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

const TONE: Record<Tone, string> = {
  neutral: 'bg-gray-100',
  success: 'bg-success-100',
  warning: 'bg-warning-100',
  danger: 'bg-error-100',
  info: 'bg-info-100',
}

const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-gray-700',
  success: 'text-success-700',
  warning: 'text-warning-700',
  danger: 'text-error-700',
  info: 'text-info-700',
}

/**
 * El color del punto de "en vivo", uno por tono.
 *
 * Van como literales y no por clase porque el halo se pinta con `style`: es un
 * `backgroundColor` que se anima, y en `react-native-css` una clase y un estilo
 * sobre la misma propiedad se pisan en silencio. Son los mismos valores de
 * `global.css` (el 500 de cada escala), que es el nivel que se ve sobre el
 * fondo 100 de la pastilla — el 700 se lee casi negro a 8 pt de diámetro.
 */
const TONE_DOT: Record<Tone, string> = {
  neutral: '#89939b',
  success: '#51c075',
  warning: '#efc482',
  danger: '#c93b2b',
  info: '#294dec',
}

export function Badge({
  label,
  tone = 'neutral',
  live = false,
  className,
}: {
  label: string
  tone?: Tone
  /**
   * Late. Es para los estados que están pasando ahora —el chaleco grabando—
   * y no para los que solo describen una situación.
   */
  live?: boolean
  className?: string
}) {
  return (
    <View
      className={cn(
        'flex-row items-center self-start rounded-full px-3 py-1.5',
        live && 'gap-2',
        TONE[tone],
        className,
      )}
    >
      {live ? <LiveDot color={TONE_DOT[tone]} /> : null}
      <Text numberOfLines={1} className={cn('text-[13px] font-semibold', TONE_TEXT[tone])}>
        {label}
      </Text>
    </View>
  )
}

/**
 * El punto que late, con su halo.
 *
 * Son dos círculos y no uno parpadeando: un punto que prende y apaga se lee
 * como una falla —es lo que hacen los LEDs de error— mientras que una onda que
 * sale del punto y se abre se lee como algo que está emitiendo. Es la misma
 * gramática del indicador de grabación de iOS, y acá dice literalmente eso: el
 * chaleco está transmitiendo mientras el paciente mira la pantalla.
 *
 * El halo nace y muere dentro del padding de la pastilla (12 pt de lado contra
 * los 8 del punto al triple de escala), así que no hace falta recortarlo ni
 * empujar el texto para dejarle lugar.
 */
function LiveDot({ color }: { color: string }) {
  const progress = usePulse()

  const halo = useAnimatedStyle(() => ({
    transform: [{ scale: interpolate(progress.value, [0, 1], [1, 3]) }],
    // Aparece de golpe y se apaga despacio: la onda tiene que salir del punto,
    // no materializarse a mitad de camino.
    opacity: interpolate(progress.value, [0, 0.1, 1], [0, 0.35, 0]),
  }))

  // El punto acompaña con un respiro mínimo. Sin esto el centro queda inmóvil
  // y el halo parece dibujado encima en vez de salir de ahí.
  const core = useAnimatedStyle(() => ({
    transform: [{ scale: interpolate(progress.value, [0, 0.1, 0.5, 1], [1, 1.18, 1, 1]) }],
  }))

  return (
    <View className="size-2 items-center justify-center">
      <AnimatedView
        pointerEvents="none"
        className="absolute size-2 rounded-full"
        style={[{ backgroundColor: color }, halo]}
      />
      <AnimatedView className="size-2 rounded-full" style={[{ backgroundColor: color }, core]} />
    </View>
  )
}
