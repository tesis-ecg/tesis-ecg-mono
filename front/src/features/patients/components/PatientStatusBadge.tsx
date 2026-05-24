import { Badge } from '@/components/ui/badge'

import type { PatientStudyStatus } from '../types'

interface PatientStatusBadgeProps {
  status: PatientStudyStatus
}

const VARIANT: Record<
  PatientStudyStatus,
  { label: string; variant: 'success' | 'warning' | 'info' | 'neutral' }
> = {
  active: { label: 'Activo', variant: 'success' },
  paused: { label: 'Pausado', variant: 'warning' },
  completed: { label: 'Completado', variant: 'info' },
  none: { label: 'Sin estudio', variant: 'neutral' },
}

export function PatientStatusBadge({ status }: PatientStatusBadgeProps) {
  const { label, variant } = VARIANT[status]
  return <Badge variant={variant}>{label}</Badge>
}
