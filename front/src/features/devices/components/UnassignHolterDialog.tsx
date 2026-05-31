import { Unlink } from 'lucide-react'
import { useState } from 'react'
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
import { unwrapError } from '@/lib/api'

import { useUnassignHolter } from '../hooks/useUnassignHolter'

interface UnassignHolterDialogProps {
  holterId: string
  serial: string
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function UnassignHolterDialog({
  holterId,
  serial,
  open,
  onOpenChange,
}: UnassignHolterDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const unassign = useUnassignHolter()

  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
  }

  const handleUnassign = () => {
    unassign.mutate(holterId, {
      onSuccess: () => {
        toast.success(`Holter ${serial} desasignado.`)
        setOpen(false)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button variant="outline">
            <Unlink className="mr-1 size-4" aria-hidden />
            Desasignar Holter
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Desasignar Holter</DialogTitle>
          <DialogDescription>
            ¿Querés desasignar el Holter <strong>{serial}</strong>? Volverá a estar disponible para
            otro paciente.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button onClick={handleUnassign} disabled={unassign.isPending}>
            {unassign.isPending ? 'Desasignando…' : 'Desasignar'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
