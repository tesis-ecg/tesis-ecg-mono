import { Badge } from '@/components/ui/badge'

import type { AlertSeverity } from '../types'

const VARIANT: Record<
  AlertSeverity,
  { label: string; variant: 'destructive' | 'warning' | 'info' | 'neutral' }
> = {
  critical: { label: 'Crítica', variant: 'destructive' },
  high: { label: 'Alta', variant: 'warning' },
  medium: { label: 'Media', variant: 'info' },
  low: { label: 'Baja', variant: 'neutral' },
}

export function AlertSeverityBadge({ severity }: { severity: AlertSeverity }) {
  const { label, variant } = VARIANT[severity]
  return <Badge variant={variant}>{label}</Badge>
}
