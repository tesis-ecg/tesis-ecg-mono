import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { Toaster as Sonner, type ToasterProps } from 'sonner'

/**
 * Los toasts del portal.
 *
 * Tres cosas se deciden acá y no en cada llamador:
 *
 * - **El fondo es blanco sólido.** Antes salía de `var(--popover)`, que sobre el
 *   fondo claro del shell dejaba el aviso a medio camino entre superficie y
 *   papel. Un mensaje que aparece sobre una tabla tiene que leerse sin competir
 *   con lo que hay debajo.
 * - **El ícono lleva el color del tipo, no el texto.** El cuerpo se queda en el
 *   gris del resto de la UI: teñir el texto entero de rojo grita más de lo que
 *   dice, y con cuatro tipos el aviso pasaba a ser un semáforo.
 * - **Los tonos son los de `ui/badge`.** El verde/ámbar/rojo del sistema ya está
 *   definido ahí; repetirlo con otros valores haría que "éxito" se vea distinto
 *   según de dónde salga.
 *
 * El ámbar de la rampa (`--color-warning-500`, `#efc482`) no contrasta sobre
 * blanco y el `700` se lee marrón oscuro, así que la advertencia usa el
 * `warning-600` que se agregó en `styles/tokens.css` para tapar ese hueco.
 */
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light"
      className="toaster group"
      position="top-right"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      toastOptions={{
        classNames: {
          toast: 'shadow-lg',
          icon: 'rounded-full',
          success: '[&_[data-icon]]:text-success-500',
          warning: '[&_[data-icon]]:text-warning-600',
          error: '[&_[data-icon]]:text-error-700',
          info: '[&_[data-icon]]:text-info-500',
        },
      }}
      style={
        {
          '--normal-bg': 'var(--color-white)',
          '--normal-text': 'var(--color-gray-900)',
          '--normal-border': 'var(--color-border)',
          '--border-radius': 'var(--radius-xl)',
        } as React.CSSProperties
      }
      {...props}
    />
  )
}

export { Toaster }
