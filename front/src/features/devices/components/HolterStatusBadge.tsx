import { Badge } from '@/components/ui/badge'

import type { HolterStatus } from '../types'

interface HolterStatusBadgeProps {
  status: HolterStatus
}

const VARIANT: Record<
  HolterStatus,
  { label: string; variant: 'success' | 'info' | 'warning' | 'neutral' }
> = {
  available: { label: 'Disponible', variant: 'success' },
  assigned: { label: 'Asignado', variant: 'info' },
  maintenance: { label: 'Mantenimiento', variant: 'warning' },
  retired: { label: 'Retirado', variant: 'neutral' },
}

export function HolterStatusBadge({ status }: HolterStatusBadgeProps) {
  const { label, variant } = VARIANT[status]
  return <Badge variant={variant}>{label}</Badge>
}
