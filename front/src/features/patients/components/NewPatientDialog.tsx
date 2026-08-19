import { Plus } from 'lucide-react'
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
import { useAuth } from '@/features/auth/AuthContext'
import { useDoctors } from '@/features/devices/hooks/useDoctors'

import { useCreatePatient } from '../hooks/useCreatePatient'
import type { PatientFormValues } from '../patientSchema'
import { patientFormToInput } from '../utils'
import { PatientForm } from './PatientForm'

export function NewPatientDialog() {
  const [open, setOpen] = useState(false)
  const createPatient = useCreatePatient()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const doctors = useDoctors(isAdmin && open)

  const handleSubmit = (values: PatientFormValues) => {
    if (isAdmin && !values.doctorId) {
      toast.error('Seleccioná el médico responsable del paciente.')
      return
    }
    createPatient.mutate(patientFormToInput(values), {
      onSuccess: (patient) => {
        toast.success(`Paciente ${patient.fullName} creado.`)
        setOpen(false)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-1 size-4" aria-hidden />
          Nuevo paciente
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nuevo paciente</DialogTitle>
          <DialogDescription>
            Completá los datos del paciente. Podés asignar un dispositivo más tarde desde su perfil.
          </DialogDescription>
        </DialogHeader>
        <PatientForm
          onSubmit={handleSubmit}
          isSubmitting={createPatient.isPending}
          submitLabel="Crear paciente"
          requireDoctor={isAdmin}
          doctorOptions={doctors.data ?? []}
        />
      </DialogContent>
    </Dialog>
  )
}
