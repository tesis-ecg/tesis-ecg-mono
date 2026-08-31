import { Text, type TextProps } from '@/tw'
import { cn } from '@/lib/cn'

/**
 * Escala tipográfica de la app.
 *
 * El piso es 17 pt (`Body`) y no los 14 habituales: el usuario objetivo tiene
 * entre 40 y 70 años y muchos leen sin anteojos de cerca. `allowFontScaling`
 * queda en su default (`true`) a propósito — si el paciente agrandó la letra en
 * los ajustes del sistema, la app lo tiene que respetar.
 */

export function Display({ className, ...props }: TextProps) {
  return <Text className={cn('text-[34px] leading-[40px] font-bold text-gray-900', className)} {...props} />
}

export function Title({ className, ...props }: TextProps) {
  return <Text className={cn('text-[28px] leading-[34px] font-bold text-gray-900', className)} {...props} />
}

export function Heading({ className, ...props }: TextProps) {
  return <Text className={cn('text-[22px] leading-[28px] font-semibold text-gray-900', className)} {...props} />
}

export function Body({ className, ...props }: TextProps) {
  return <Text className={cn('text-[17px] leading-[24px] text-gray-900', className)} {...props} />
}

export function Callout({ className, ...props }: TextProps) {
  return <Text className={cn('text-[15px] leading-[21px] text-gray-700', className)} {...props} />
}

export function Caption({ className, ...props }: TextProps) {
  return <Text className={cn('text-[13px] leading-[18px] text-gray-600', className)} {...props} />
}
