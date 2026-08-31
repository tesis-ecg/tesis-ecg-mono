/**
 * Primitivos de React Native con soporte de `className`.
 *
 * `react-native-css` no parchea los componentes globalmente (el metro config
 * usa `globalClassNamePolyfill: false`), así que cada primitivo que se quiera
 * estilar con Tailwind tiene que envolverse acá con `useCssElement`. Todo el
 * resto de la app importa desde `@/tw` y nunca desde `react-native`.
 */
import React from 'react'
import {
  ActivityIndicator as RNActivityIndicator,
  Pressable as RNPressable,
  ScrollView as RNScrollView,
  Text as RNText,
  TextInput as RNTextInput,
  View as RNView,
} from 'react-native'
import Animated from 'react-native-reanimated'
import { useCssElement } from 'react-native-css'

/**
 * `useCssElement` con el componente ya ensanchado.
 *
 * Su firma infiere el tipo literal del componente y sobre eso calcula el mapeo
 * de props. Para `Pressable` y `ScrollView`, que tienen decenas de props, el
 * cálculo revienta con TS2590 ("union type too complex"). Pasar el componente
 * como `ComponentType<any>` corta esa inferencia; el chequeo de las props del
 * llamador no se pierde, porque lo hace el tipo de cada wrapper de abajo.
 */
function useStyled(
  component: React.ComponentType<any>,
  props: object,
  mapping: Record<string, string>,
): React.ReactElement {
  return useCssElement(component, props as any, mapping as any)
}

export type ViewProps = React.ComponentProps<typeof RNView> & { className?: string }

export const View = (props: ViewProps): React.ReactElement =>
  useStyled(RNView, props, { className: 'style' })
View.displayName = 'CSS(View)'

export type TextProps = React.ComponentProps<typeof RNText> & { className?: string }

export const Text = (props: TextProps): React.ReactElement =>
  useStyled(RNText, props, { className: 'style' })
Text.displayName = 'CSS(Text)'

export type PressableProps = React.ComponentProps<typeof RNPressable> & { className?: string }

export const Pressable = (props: PressableProps): React.ReactElement =>
  useStyled(RNPressable, props, { className: 'style' })
Pressable.displayName = 'CSS(Pressable)'

export type ScrollViewProps = React.ComponentProps<typeof RNScrollView> & {
  className?: string
  contentContainerClassName?: string
}

export const ScrollView = (props: ScrollViewProps): React.ReactElement =>
  useStyled(RNScrollView, props, {
    className: 'style',
    contentContainerClassName: 'contentContainerStyle',
  })
ScrollView.displayName = 'CSS(ScrollView)'

export type TextInputProps = React.ComponentProps<typeof RNTextInput> & {
  className?: string
  // React 19 pasa `ref` como una prop más; el tipo de RN todavía no lo
  // refleja, así que se declara acá para que `Field` pueda reenviarlo.
  ref?: React.Ref<RNTextInput>
}

export const TextInput = (props: TextInputProps): React.ReactElement =>
  useStyled(RNTextInput, props, { className: 'style' })
TextInput.displayName = 'CSS(TextInput)'

export type ActivityIndicatorProps = React.ComponentProps<typeof RNActivityIndicator> & {
  className?: string
}

export const ActivityIndicator = (props: ActivityIndicatorProps): React.ReactElement =>
  useStyled(RNActivityIndicator, props, { className: 'style' })
ActivityIndicator.displayName = 'CSS(ActivityIndicator)'

/**
 * Versiones animadas, para Reanimated.
 *
 * Van acá y no en cada pantalla por la misma razón que el resto del módulo: un
 * `Animated.View` importado directo de Reanimated no entiende `className`, y
 * mezclarlo con los de `@/tw` haría que la mitad de la app se estile con
 * `style` y la otra con clases.
 *
 * El `style` sigue disponible y es por donde entran los `useAnimatedStyle`:
 * `react-native-css` compone lo que venga por `className` con lo que venga por
 * `style`, en ese orden, así que la animación siempre pisa a la clase.
 */
export type AnimatedViewProps = React.ComponentProps<typeof Animated.View> & {
  className?: string
}

export const AnimatedView = (props: AnimatedViewProps): React.ReactElement =>
  useStyled(Animated.View, props, { className: 'style' })
AnimatedView.displayName = 'CSS(Animated.View)'

const RNAnimatedPressable = Animated.createAnimatedComponent(RNPressable)

export type AnimatedPressableProps = React.ComponentProps<typeof RNAnimatedPressable> & {
  className?: string
}

export const AnimatedPressable = (props: AnimatedPressableProps): React.ReactElement =>
  useStyled(RNAnimatedPressable, props, { className: 'style' })
AnimatedPressable.displayName = 'CSS(Animated.Pressable)'
