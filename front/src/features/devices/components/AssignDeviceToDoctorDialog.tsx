import { Stethoscope } from 'lucide-react'
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

import { useAssignHolterToDoctor } from '../hooks/useAssignHolterToDoctor'
import { useDoctors } from '../hooks/useDoctors'
import type { Holter } from '../types'

interface AssignDeviceToDoctorDialogProps {
  holter: Holter
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Asigna un dispositivo a un médico específico (solo admin). */
export function AssignDeviceToDoctorDialog({
  holter,
  open,
  onOpenChange,
}: AssignDeviceToDoctorDialogProps) {
  const [selectedDoctorId, setSelectedDoctorId] = useState<string>(holter.assignedDoctorId ?? '')
  const doctors = useDoctors()
  const assign = useAssignHolterToDoctor()

  const setOpen = (next: boolean) => {
    onOpenChange(next)
    if (!next) setSelectedDoctorId(holter.assignedDoctorId ?? '')
  }

  const handleAssign = () => {
    if (!selectedDoctorId) return
    assign.mutate(
      { holterId: holter.id, doctorId: selectedDoctorId },
      {
        onSuccess: () => {
          toast.success(`Holter ${holter.serial} asignado al médico.`)
          setOpen(false)
        },
        onError: (error) => {
          toast.error(unwrapError(error))
        },
      },
    )
  }

  const items = doctors.data ?? []
  const hasOptions = items.length > 0

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Asignar Holter a médico</DialogTitle>
          <DialogDescription>
            Elegí el médico al que pertenecerá el Holter <strong>{holter.serial}</strong>. El médico
            podrá asignarlo a sus pacientes.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <Label htmlFor="assign-doctor-select">Médico</Label>
          <Select value={selectedDoctorId} onValueChange={setSelectedDoctorId}>
            <SelectTrigger id="assign-doctor-select" className="w-full">
              <SelectValue
                placeholder={
                  doctors.isLoading
                    ? 'Cargando…'
                    : hasOptions
                      ? 'Seleccioná un médico'
                      : 'No hay médicos disponibles'
                }
              />
            </SelectTrigger>
            <SelectContent>
              {items.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.fullName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button onClick={handleAssign} disabled={!selectedDoctorId || assign.isPending}>
            {assign.isPending ? (
              'Asignando…'
            ) : (
              <>
                <Stethoscope className="mr-1 size-4" aria-hidden />
                Asignar
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
