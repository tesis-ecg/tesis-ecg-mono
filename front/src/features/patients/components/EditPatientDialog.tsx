import { Pencil } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { unwrapError } from '@/lib/api'

import { useUpdatePatient } from '../hooks/useUpdatePatient'
import type { PatientFormValues } from '../patientSchema'
import type { Patient, UpdatePatientInput } from '../types'
import { patientFormToInput, patientToFormValues } from '../utils'
import { PatientForm } from './PatientForm'

/** Devuelve solo los campos cuyo valor cambió respecto del paciente original. */
function diffPatient(patient: Patient, values: PatientFormValues): UpdatePatientInput {
  const next = patientFormToInput(values)
  const changed: UpdatePatientInput = {}
  if (next.fullName !== patient.fullName) changed.fullName = next.fullName
  if (next.dni !== patient.dni) changed.dni = next.dni
  if (next.birthDate !== patient.birthDate) changed.birthDate = next.birthDate
  if (next.sex !== patient.sex) changed.sex = next.sex
  if (next.contactEmail !== patient.contactEmail) changed.contactEmail = next.contactEmail
  if (next.contactPhone !== patient.contactPhone) changed.contactPhone = next.contactPhone
  return changed
}

interface EditPatientDialogProps {
  patient: Patient
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function EditPatientDialog({ patient, open, onOpenChange }: EditPatientDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
  }
  const updatePatient = useUpdatePatient()

  const handleSubmit = (values: PatientFormValues) => {
    const input = diffPatient(patient, values)
    if (Object.keys(input).length === 0) {
      setOpen(false)
      return
    }
    updatePatient.mutate(
      { id: patient.id, input },
      {
        onSuccess: () => {
          toast.success('Paciente actualizado.')
          setOpen(false)
        },
        onError: (error) => {
          toast.error(unwrapError(error))
        },
      },
    )
  }

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button variant="outline">
            <Pencil className="mr-1 size-4" aria-hidden />
            Editar
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar paciente</DialogTitle>
          <DialogDescription>Modificá los datos del paciente.</DialogDescription>
        </DialogHeader>
        <PatientForm
          defaultValues={patientToFormValues(patient)}
          onSubmit={handleSubmit}
          isSubmitting={updatePatient.isPending}
          submitLabel="Guardar cambios"
        />
      </DialogContent>
    </Dialog>
  )
}
