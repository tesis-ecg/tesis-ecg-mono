import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { usePatient } from '@/features/patients/hooks/usePatient'
import { unwrapError } from '@/lib/api'

import { useDeleteHolter } from '../hooks/useDeleteHolter'
import type { Holter } from '../types'

interface DeleteHolterDialogProps {
  holter: Holter
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Si true, al borrar exitosamente navega a `/devices` (útil desde el detalle). */
  navigateOnSuccess?: boolean
}

export function DeleteHolterDialog({
  holter,
  open,
  onOpenChange,
  navigateOnSuccess = false,
}: DeleteHolterDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
  }
  const navigate = useNavigate()
  const deleteHolter = useDeleteHolter()
  const patient = usePatient(holter.assignedPatientId ?? undefined)

  const handleDelete = () => {
    deleteHolter.mutate(holter.id, {
      onSuccess: () => {
        toast.success(`Holter ${holter.serial} retirado.`)
        setOpen(false)
        if (navigateOnSuccess) navigate('/devices')
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  const patientName = patient.data?.fullName ?? holter.assignedPatientId

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button variant="outline" className="text-destructive hover:text-destructive">
            <Trash2 className="mr-1 size-4" aria-hidden />
            Eliminar
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Eliminar Holter</DialogTitle>
          <DialogDescription>
            El Holter <strong>{holter.serial}</strong> se marcará como retirado. Se preserva el
            historial de estudios, pero no podrás asignarlo de nuevo.
            {holter.assignedPatientId && (
              <>
                {' '}
                Actualmente está asignado a <strong>{patientName}</strong> — al eliminarlo, el
                paciente queda sin Holter.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button variant="destructive" onClick={handleDelete} disabled={deleteHolter.isPending}>
            {deleteHolter.isPending ? 'Eliminando…' : 'Eliminar Holter'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
