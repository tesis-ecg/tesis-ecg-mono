import { ArrowRightLeft } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
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
import { usePatient } from '@/features/patients/hooks/usePatient'
import { useUnassignedPatients } from '@/features/patients/hooks/useUnassignedPatients'
import { unwrapError } from '@/lib/api'

import { useReassignHolter } from '../hooks/useReassignHolter'
import type { Holter } from '../types'

interface ReassignHolterDialogProps {
  holter: Holter
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function ReassignHolterDialog({ holter, open, onOpenChange }: ReassignHolterDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const [selectedPatientId, setSelectedPatientId] = useState<string>('')
  const available = useUnassignedPatients()
  const currentPatient = usePatient(holter.assignedPatientId ?? undefined)
  const reassign = useReassignHolter()

  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
    if (!next) setSelectedPatientId('')
  }

  const handleReassign = () => {
    if (!selectedPatientId) return
    reassign.mutate(
      { holterId: holter.id, newPatientId: selectedPatientId },
      {
        onSuccess: () => {
          toast.success(`Holter ${holter.serial} reasignado.`)
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
  const isAssigned = holter.assignedPatientId !== null
  const title = isAssigned ? 'Cambiar paciente asignado' : 'Asignar Holter a paciente'
  const description = isAssigned
    ? `Mover el Holter ${holter.serial} a otro paciente. El paciente anterior queda sin Holter automáticamente.`
    : `Elegí un paciente para asignarle el Holter ${holter.serial}.`
  const submitLabel = isAssigned ? 'Reasignar' : 'Asignar'
  const submitPending = isAssigned ? 'Reasignando…' : 'Asignando…'

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button variant="outline">
            <ArrowRightLeft className="mr-1 size-4" aria-hidden />
            {isAssigned ? 'Cambiar paciente' : 'Asignar a paciente'}
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {holter.assignedPatientId && (
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-body3 text-gray-700">
            Actualmente asignado a:{' '}
            {currentPatient.data ? (
              <Link
                to={`/patients/${holter.assignedPatientId}`}
                className="font-medium text-primary-500 hover:underline"
              >
                {currentPatient.data.fullName}
              </Link>
            ) : (
              <span className="font-mono">{holter.assignedPatientId}</span>
            )}
          </div>
        )}

        <div className="grid gap-2">
          <Label htmlFor="reassign-patient-select">Nuevo paciente</Label>
          <Select value={selectedPatientId} onValueChange={setSelectedPatientId}>
            <SelectTrigger id="reassign-patient-select" className="w-full">
              <SelectValue
                placeholder={
                  available.isLoading
                    ? 'Cargando…'
                    : hasOptions
                      ? 'Seleccioná un paciente sin Holter'
                      : 'No hay pacientes sin Holter'
                }
              />
            </SelectTrigger>
            <SelectContent>
              {items.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.fullName} · DNI {p.dni}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!available.isLoading && !hasOptions && (
            <p className="text-body3 text-gray-600">
              Todos los pacientes ya tienen un Holter asignado.
            </p>
          )}
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button onClick={handleReassign} disabled={!selectedPatientId || reassign.isPending}>
            {reassign.isPending ? submitPending : submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
