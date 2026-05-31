import { Link2 } from 'lucide-react'
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
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { unwrapError } from '@/lib/api'

import { useAssignHolter } from '../hooks/useAssignHolter'
import { useAvailableHolters } from '../hooks/useAvailableHolters'

interface AssignHolterDialogProps {
  patientId: string
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function AssignHolterDialog({ patientId, open, onOpenChange }: AssignHolterDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const [selectedHolterId, setSelectedHolterId] = useState<string>('')
  const available = useAvailableHolters()
  const assign = useAssignHolter()

  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
    if (!next) setSelectedHolterId('')
  }

  const handleAssign = () => {
    if (!selectedHolterId) return
    assign.mutate(
      { holterId: selectedHolterId, patientId },
      {
        onSuccess: (holter) => {
          toast.success(`Holter ${holter.serial} asignado.`)
          setOpen(false)
        },
        onError: (error) => {
          toast.error(unwrapError(error))
        },
      },
    )
  }

  const items = available.data?.items ?? []
  const hasOptions = items.length > 0

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button>
            <Link2 className="mr-1 size-4" aria-hidden />
            Asignar Holter
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Asignar Holter</DialogTitle>
          <DialogDescription>
            Elegí un Holter disponible para asignarle al paciente. Una vez asignado, el dispositivo
            queda vinculado hasta que lo desasignes.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <Label htmlFor="holter-select">Holter disponible</Label>
          <Select value={selectedHolterId} onValueChange={setSelectedHolterId}>
            <SelectTrigger id="holter-select" className="w-full">
              <SelectValue
                placeholder={
                  available.isLoading
                    ? 'Cargando…'
                    : hasOptions
                      ? 'Seleccioná un Holter'
                      : 'No hay Holters disponibles'
                }
              />
            </SelectTrigger>
            <SelectContent>
              {items.map((h) => (
                <SelectItem key={h.id} value={h.id}>
                  {h.serial} · {h.model}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!available.isLoading && !hasOptions && (
            <p className="text-body3 text-gray-600">
              No hay Holters libres. Agregá uno desde la sección Dispositivos.
            </p>
          )}
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button onClick={handleAssign} disabled={!selectedHolterId || assign.isPending}>
            {assign.isPending ? 'Asignando…' : 'Asignar Holter'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
