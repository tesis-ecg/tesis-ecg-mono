import { View, type ViewProps } from '@/tw'
import { cn } from '@/lib/cn'

/**
 * Superficie blanca sobre el fondo gris.
 *
 * Sin bordes: la separación la da el contraste con el fondo y una sombra muy
 * suave, que es lo que hace que el contenido flote en vez de quedar encajonado.
 */
export function Card({ className, ...props }: ViewProps) {
  return (
    <View
      className={cn(
        'rounded-[20px] bg-white p-5',
        'shadow-[0px_2px_12px_rgba(23,45,57,0.06)]',
        className,
      )}
      {...props}
    />
  )
}
