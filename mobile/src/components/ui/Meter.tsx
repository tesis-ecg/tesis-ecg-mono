import { View } from '@/tw'
import { cn } from '@/lib/cn'

interface MeterProps {
  /** 0 a 100. `null` cuando el equipo todavía no reportó el dato. */
  value: number | null
  className?: string
}

/**
 * Barra de nivel, hoy solo para la batería del chaleco.
 *
 * Una barra y no un anillo: el anillo se ve más lindo en una captura, pero
 * "cuánto queda" se lee más rápido en algo que se llena de izquierda a derecha,
 * y el público de la app no está para descifrar un gráfico.
 *
 * El color lo decide el nivel y no el estado del equipo: un 15% es un 15% aunque
 * todo lo demás esté bien, y es justo el momento en que hay que decírselo.
 */
export function Meter({ value, className }: MeterProps) {
  if (value === null) {
    return (
      <View className={cn('h-2 w-full overflow-hidden rounded-full bg-gray-100', className)} />
    )
  }

  const clamped = Math.max(0, Math.min(100, value))
  const tone =
    clamped <= 15 ? 'bg-error-400' : clamped <= 35 ? 'bg-warning-500' : 'bg-success-500'

  return (
    <View className={cn('h-2 w-full overflow-hidden rounded-full bg-gray-100', className)}>
      <View className={cn('h-full rounded-full', tone)} style={{ width: `${clamped}%` }} />
    </View>
  )
}
