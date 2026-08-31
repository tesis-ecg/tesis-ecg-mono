import type { LucideIcon } from 'lucide-react-native'

import { View } from '@/tw'
import { cn } from '@/lib/cn'

import { Body, Caption } from './typography'

interface InfoRowProps {
  icon: LucideIcon
  label: string
  value: string
  /** Segunda línea, más chica: la fecha exacta debajo de un "hace 1 hora". */
  hint?: string
  /**
   * Separador inferior. Se pasa a mano y no con `last:border-b-0` porque en
   * React Native no existen los selectores de hermanos: la variante `last:` no
   * hace nada y dejaba una línea colgando debajo de la última fila.
   */
  divider?: boolean
}

/** Fila de dato dentro de una card. Compartida por "Dispositivo" y "Perfil". */
export function InfoRow({ icon: Icon, label, value, hint, divider = true }: InfoRowProps) {
  return (
    <View
      className={cn(
        'flex-row items-center gap-4 py-4',
        divider && 'border-b border-gray-100',
      )}
    >
      <View className="size-11 items-center justify-center rounded-full bg-gray-50">
        <Icon size={20} color="#5c6b74" />
      </View>
      <View className="flex-1 gap-0.5">
        <Caption>{label}</Caption>
        <Body className="font-semibold">{value}</Body>
        {hint ? <Caption className="text-gray-500">{hint}</Caption> : null}
      </View>
    </View>
  )
}
