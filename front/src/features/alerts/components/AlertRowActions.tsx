import { Check } from 'lucide-react'
import { toast } from 'sonner'

import { TableRowActions, type TableRowAction } from '@/components/ui/table'
import { unwrapError } from '@/lib/api'

import { useAcknowledgeAlert } from '../hooks/useAcknowledgeAlert'
import type { Alert } from '../types'

/**
 * Acciones de una fila de la bandeja de alertas.
 *
 * La mutación vive acá y no en la página para que el estado `isPending` sea el
 * de esta fila y no congele la tabla entera mientras se guarda.
 */
export function AlertRowActions({ alert }: { alert: Alert }) {
  const acknowledge = useAcknowledgeAlert()

  // Una alerta ya leída no tiene nada más que hacer: la fila navega al estudio
  // por su cuenta, así que el kebab sobra.
  if (alert.acknowledgedAt) return null

  const actions: TableRowAction[] = [
    {
      label: acknowledge.isPending ? 'Guardando…' : 'Marcar como leída',
      icon: Check,
      disabled: acknowledge.isPending,
      onSelect: () =>
        acknowledge.mutate(alert.id, {
          onSuccess: () => toast.success(`Alerta de ${alert.patientName} marcada como leída.`),
          onError: (error) => toast.error(unwrapError(error)),
        }),
    },
  ]

  // `TableRowActions` ya frena la propagación, así que el click sobre el kebab
  // no dispara la navegación de la fila.
  return (
    <TableRowActions label={`Acciones de la alerta de ${alert.patientName}`} actions={actions} />
  )
}
