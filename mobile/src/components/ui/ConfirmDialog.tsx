import type { LucideIcon } from 'lucide-react-native'
import { useEffect, useState } from 'react'
import { Modal } from 'react-native'
import { FadeIn, FadeOut, ReduceMotion, ZoomIn } from 'react-native-reanimated'

import { Button } from '@/components/ui/Button'
import { Body, Heading } from '@/components/ui/typography'
import { AnimatedView, Pressable, View } from '@/tw'

/** Tiene que cubrir la animación de salida; si no, el diálogo desaparece de golpe. */
const EXIT_MS = 160

interface ConfirmDialogProps {
  visible: boolean
  title: string
  message: string
  confirmLabel: string
  cancelLabel?: string
  /** `danger` para lo que no se deshace: cerrar sesión, descartar algo escrito. */
  tone?: 'primary' | 'danger'
  icon?: LucideIcon
  loading?: boolean
  onConfirm: () => void
  onClose: () => void
}

/**
 * Confirmación de una acción que no se deshace.
 *
 * Propia y no `Alert.alert`, por la regla de siempre: el alert del sistema se
 * dibuja distinto en iOS y en Android, y su texto sale a 13 pt con botones de
 * 30 —la mitad del piso que se fijó para esta app—. Acá los dos botones son los
 * mismos `ui/Button` de 56 pt del resto de las pantallas.
 *
 * Van apilados y no lado a lado: en horizontal el destructivo y el de cancelar
 * quedan del mismo tamaño y a un pulgar apurado le da igual cuál toca. Apilados
 * hay que leer para elegir, que es justo lo que un diálogo de confirmación está
 * pidiendo.
 *
 * El `Modal` se queda montado unos milisegundos de más al cerrar por lo mismo
 * que `ui/BottomSheet`: es lo que le da lugar a la animación de salida.
 */
export function ConfirmDialog({
  visible,
  title,
  message,
  confirmLabel,
  cancelLabel = 'Cancelar',
  tone = 'danger',
  icon: Icon,
  loading = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [isExiting, setIsExiting] = useState(false)
  const [wasVisible, setWasVisible] = useState(visible)

  // Ajuste de estado durante el render, igual que en `ui/BottomSheet`: es el
  // patrón que recomienda React para derivar estado de un cambio de prop.
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
      // Igual que en `ui/BottomSheet`: sin esto el velo corta justo arriba de la
      // barra de navegación de Android y deja una franja clara abajo de todo.
      navigationBarTranslucent
      onRequestClose={onClose}
    >
      <View className="flex-1 items-center justify-center px-6">
        {visible ? (
          <>
            <AnimatedView
              entering={FadeIn.duration(140).reduceMotion(ReduceMotion.System)}
              exiting={FadeOut.duration(140).reduceMotion(ReduceMotion.System)}
              className="absolute inset-0 bg-scrim"
            >
              {/* El velo se pinta y se anima; el toque lo escucha esta capa. */}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Cerrar"
                onPress={loading ? undefined : onClose}
                className="flex-1"
              />
            </AnimatedView>

            <AnimatedView
              // Crece desde el 92 % y no desde cero: `ZoomIn` sin tocar arranca
              // en escala 0 y el diálogo aparece como un globo que se infla, no
              // como una pregunta. Y sale sólo con un fundido: encogerse hasta
              // desaparecer sería una segunda animación para decir lo mismo.
              entering={ZoomIn.withInitialValues({ transform: [{ scale: 0.92 }] })
                .duration(180)
                .reduceMotion(ReduceMotion.System)}
              exiting={FadeOut.duration(EXIT_MS).reduceMotion(ReduceMotion.System)}
              className="w-full max-w-[420px] items-center gap-3 rounded-[28px] bg-white p-6"
              accessibilityViewIsModal
            >
              {Icon ? (
                <View
                  className={`size-14 items-center justify-center rounded-full ${
                    tone === 'danger' ? 'bg-error-100' : 'bg-primary-50'
                  }`}
                >
                  <Icon size={26} color={tone === 'danger' ? '#88271d' : '#0b2185'} />
                </View>
              ) : null}

              <Heading className="pt-1 text-center">{title}</Heading>
              <Body className="pb-2 text-center text-gray-700">{message}</Body>

              <Button
                label={confirmLabel}
                variant={tone === 'danger' ? 'danger' : 'primary'}
                loading={loading}
                onPress={onConfirm}
              />
              <Button
                label={cancelLabel}
                variant="ghost"
                disabled={loading}
                onPress={onClose}
              />
            </AnimatedView>
          </>
        ) : null}
      </View>
    </Modal>
  )
}
