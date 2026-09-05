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
  /** Si el equipo está grabando, desasignarlo cierra ese estudio. Hay que decirlo. */
  hasActiveStudy?: boolean
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function UnassignHolterDialog({
  holterId,
  serial,
  hasActiveStudy = false,
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
        // Desasignar cierra el estudio abierto del paciente (lo hace
        // `close_open_studies_for_device` en el backend). Salió bien, pero se
        // llevó algo puesto: eso es una advertencia, no una confirmación a secas.
        if (hasActiveStudy) {
          toast.warning(`Holter ${serial} desasignado. El estudio en curso quedó finalizado.`)
        } else {
          toast.success(`Holter ${serial} desasignado.`)
        }
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
            {hasActiveStudy && (
              <>
                {' '}
                <strong>
                  El estudio que está grabando se cierra como finalizado y no se puede reabrir.
                </strong>
              </>
            )}
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
