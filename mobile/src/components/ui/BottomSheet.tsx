import { X } from 'lucide-react-native'
import { useEffect, useState } from 'react'
import { Modal, useWindowDimensions } from 'react-native'
import {
  FadeIn,
  FadeOut,
  ReduceMotion,
  SlideInDown,
  SlideOutDown,
} from 'react-native-reanimated'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { AnimatedView, Pressable, ScrollView, View } from '@/tw'
import { Heading } from '@/components/ui/typography'
import { cn } from '@/lib/cn'

interface BottomSheetProps {
  visible: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  /** Barra fija al pie de la hoja: el "Listo" de una selección múltiple. */
  footer?: React.ReactNode
  contentClassName?: string
}

/** Cuánto de la pantalla puede ocupar la hoja como máximo. */
const MAX_HEIGHT_RATIO = 0.82

/** Tiene que cubrir la animación de salida; si no, la hoja desaparece de golpe. */
const EXIT_MS = 220

/**
 * Hoja que sube desde abajo.
 *
 * Existe para sacar las listas largas del formulario: con veinte síntomas
 * dibujados en la pantalla, el botón de enviar quedaba a tres pantallazos de
 * distancia y el paciente perdía de vista qué estaba contestando.
 *
 * El `Modal` va sin animación propia y el movimiento lo hacen el velo y la hoja
 * por separado. Con `animationType="slide"` el velo oscuro sube junto con la
 * hoja, y una sombra que entra deslizándose se lee como una segunda superficie
 * en vez de como el fondo que es. Por eso el `Modal` se queda montado unos
 * milisegundos de más al cerrar: es lo que le da lugar a la animación de salida.
 */
export function BottomSheet({
  visible,
  onClose,
  title,
  children,
  footer,
  contentClassName,
}: BottomSheetProps) {
  const insets = useSafeAreaInsets()
  const { height } = useWindowDimensions()
  const [isExiting, setIsExiting] = useState(false)
  const [wasVisible, setWasVisible] = useState(visible)

  // Ajuste de estado durante el render y no en un efecto: es el patrón que
  // recomienda React para derivar estado de un cambio de prop, y evita el
  // render de más que deja un parpadeo entre que la hoja se cierra y el
  // `Modal` se entera.
  if (visible !== wasVisible) {
    setWasVisible(visible)
    setIsExiting(!visible)
  }

  useEffect(() => {
    if (!isExiting) return
    const timer = setTimeout(() => setIsExiting(false), EXIT_MS)
    return () => clearTimeout(timer)
  }, [isExiting])

  if (!visible && !isExiting) return null

  return (
    <Modal
      transparent
      visible
      animationType="none"
      statusBarTranslucent
      // El botón físico de atrás de Android: sin esto la hoja queda abierta y
      // el gesto se lo come la pantalla de abajo.
      onRequestClose={onClose}
    >
      <View className="flex-1 justify-end">
        {visible ? (
          <>
            <AnimatedView
              entering={FadeIn.duration(180).reduceMotion(ReduceMotion.System)}
              exiting={FadeOut.duration(180).reduceMotion(ReduceMotion.System)}
              // 40 %: el formulario que queda atrás es texto sobre blanco, y
              // con un velo más liviano las dos superficies competían — el
              // paciente seguía leyendo el fondo en vez de las opciones. El
              // alfa vive en `--color-scrim` y no en un `bg-black/40`, que acá
              // no pinta nada; ver la trampa de `color-mix` en AGENTS.md.
              className="absolute inset-0 bg-scrim"
            >
              {/* El velo se pinta y se anima; el toque lo escucha esta capa. */}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Cerrar"
                onPress={onClose}
                className="flex-1"
              />
            </AnimatedView>

            <AnimatedView
              entering={SlideInDown.duration(260).reduceMotion(ReduceMotion.System)}
              exiting={SlideOutDown.duration(EXIT_MS).reduceMotion(ReduceMotion.System)}
              className="rounded-t-[28px] bg-white"
              style={{
                maxHeight: height * MAX_HEIGHT_RATIO,
                paddingBottom: insets.bottom + 12,
              }}
            >
              {/* El agarre: dice que esto es una hoja y no una pantalla nueva. */}
              <View className="items-center pt-3 pb-1">
                <View className="h-1 w-10 rounded-full bg-gray-200" />
              </View>

              <View className="flex-row items-center gap-3 px-5 pt-2 pb-3">
                <Heading className="flex-1">{title}</Heading>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Cerrar"
                  onPress={onClose}
                  className="size-11 items-center justify-center rounded-full bg-gray-50"
                >
                  <X size={22} color="#5c6b74" />
                </Pressable>
              </View>

              {/*
                `shrink` y no `flex-1`: un `ScrollView` de React Native no se
                encoge por default, así que dentro de una hoja con `maxHeight`
                crecía hasta pasarse y el pie quedaba fuera de la pantalla.
              */}
              <ScrollView
                className="shrink"
                contentContainerStyle={{ paddingBottom: 8 }}
                keyboardShouldPersistTaps="handled"
                showsVerticalScrollIndicator
              >
                <View className={cn('px-5', contentClassName)}>{children}</View>
              </ScrollView>

              {footer ? <View className="px-5 pt-3">{footer}</View> : null}
            </AnimatedView>
          </>
        ) : null}
      </View>
    </Modal>
  )
}
