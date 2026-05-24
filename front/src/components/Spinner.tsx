import { Loader2 } from 'lucide-react'

import { cn } from '@/lib/utils'

interface SpinnerProps {
  className?: string
  size?: 'sm' | 'md' | 'lg'
  label?: string
}

const SIZE: Record<NonNullable<SpinnerProps['size']>, string> = {
  sm: 'size-4',
  md: 'size-6',
  lg: 'size-10',
}

export function Spinner({ className, size = 'md', label }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn('inline-flex items-center gap-2', className)}
    >
      <Loader2 className={cn(SIZE[size], 'animate-spin text-primary-500')} aria-hidden />
      {label && <span className="text-body2 text-gray-600">{label}</span>}
      <span className="sr-only">{label ?? 'Cargando'}</span>
    </span>
  )
}
