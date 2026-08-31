import { useAnimatedStyle, useReducedMotion, useSharedValue, withTiming } from 'react-native-reanimated'

import { ActivityIndicator, AnimatedView, Pressable, Text, type PressableProps } from '@/tw'
import { brandGradient } from '@/lib/gradients'
import * as haptics from '@/lib/haptics'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends Omit<PressableProps, 'children' | 'style'> {
  label: string
  variant?: Variant
  loading?: boolean
  /** Ocupa todo el ancho disponible. Es el default: hay un CTA por pantalla. */
  fullWidth?: boolean
}

const SURFACE: Record<Variant, string> = {
  // El primario no lleva color por clase: se lo pone el gradiente.
  primary: '',
  secondary: 'bg-primary-50',
  ghost: 'bg-transparent',
  danger: 'bg-error-100',
}

const LABEL: Record<Variant, string> = {
  primary: 'text-white',
  secondary: 'text-primary-500',
  ghost: 'text-gray-700',
  danger: 'text-error-700',
}

/**
 * Botón de la app.
 *
 * 56 pt de alto y no los 44 mínimos de Apple: el usuario objetivo puede tener
 * menos precisión motriz, y en las pantallas hay pocas acciones, así que sobra
 * el espacio.
 *
 * **La superficie es una `View` interna y el `Pressable` queda pelado.** Con el
 * gradiente y el `useAnimatedStyle` puestos sobre el propio `Pressable`
 * animado, el botón se dibujaba bien pero `onPress` no llegaba a dispararse
 * nunca — el formulario de la bitácora quedaba imposible de enviar. Separando
 * las dos cosas, el `Pressable` solo escucha el toque y la `View` solo se
 * pinta y se anima.
 */
export function Button({
  label,
  variant = 'primary',
  loading = false,
  fullWidth = true,
  disabled,
  className,
  onPress,
  ...props
}: ButtonProps) {
  const isDisabled = disabled || loading
  const reduceMotion = useReducedMotion()
  const pressed = useSharedValue(0)

  const animated = useAnimatedStyle(() => ({
    transform: [{ scale: reduceMotion ? 1 : 1 - pressed.value * 0.03 }],
    opacity: 1 - pressed.value * 0.12,
  }))

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(isDisabled), busy: loading }}
      disabled={isDisabled}
      onPressIn={() => {
        pressed.value = withTiming(1, { duration: 90 })
      }}
      onPressOut={() => {
        pressed.value = withTiming(0, { duration: 160 })
      }}
      onPress={(event) => {
        // Confirmación táctil: en un celular en el bolsillo, o con la pantalla
        // a contraluz, es lo que le dice al paciente que el toque entró.
        haptics.selection()
        onPress?.(event)
      }}
      className={cn(fullWidth && 'w-full', isDisabled && 'opacity-40', className)}
      {...props}
    >
      <AnimatedView
        className={cn(
          'h-14 flex-row items-center justify-center gap-2 rounded-full px-6',
          SURFACE[variant],
        )}
        style={[variant === 'primary' && brandGradient, animated]}
      >
        {loading ? (
          <ActivityIndicator color={variant === 'primary' ? '#ffffff' : '#0b2185'} />
        ) : (
          <Text className={cn('text-[17px] font-semibold', LABEL[variant])}>{label}</Text>
        )}
      </AnimatedView>
    </Pressable>
  )
}
