import { Badge } from '@/components/ui/badge'

import type { PatientStudySessionStatus } from '../types'

interface StudyStatusBadgeProps {
  status: PatientStudySessionStatus
}

const VARIANT: Record<
  PatientStudySessionStatus,
  { label: string; variant: 'success' | 'info' | 'warning' | 'destructive' }
> = {
  in_progress: { label: 'En curso', variant: 'success' },
  completed: { label: 'Completado', variant: 'info' },
  cancelled: { label: 'Cancelado', variant: 'destructive' },
  scheduled: { label: 'Programado', variant: 'warning' },
}

export function StudyStatusBadge({ status }: StudyStatusBadgeProps) {
  const { label, variant } = VARIANT[status]
  return <Badge variant={variant}>{label}</Badge>
}
