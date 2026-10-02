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
import type { CreatedPatient } from '../types'
import { patientFormToInput } from '../utils'
import { PatientCredentialsDialog } from './PatientCredentialsDialog'
import { PatientForm } from './PatientForm'

export function NewPatientDialog() {
  const [open, setOpen] = useState(false)
  // La contraseña vive acá y no en el diálogo del form: al cerrarse el form se
  // desmonta, y con él se iría el único momento en que la contraseña existe.
  const [created, setCreated] = useState<CreatedPatient | null>(null)
  const createPatient = useCreatePatient()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const doctors = useDoctors(isAdmin && open)

  const handleSubmit = (values: PatientFormValues) => {
    if (isAdmin && !values.doctorId) {
      // `warning` y no `error`: no falló nada, falta un dato del formulario.
      // El rojo del error queda para lo que el sistema no pudo hacer.
      toast.warning('Seleccioná el médico responsable del paciente.')
      return
    }
    createPatient.mutate(patientFormToInput(values), {
      onSuccess: (patient) => {
        toast.success(`Paciente ${patient.fullName} creado.`)
        setOpen(false)
        setCreated(patient)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <>
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
              Completá los datos del paciente. Se le va a crear el acceso a la app con el email que
              cargues. Podés asignarle un dispositivo más tarde desde su perfil.
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

      {created && (
        <PatientCredentialsDialog
          open
          onOpenChange={(next) => !next && setCreated(null)}
          patientName={created.fullName}
          email={created.contactEmail ?? ''}
          password={created.generatedPassword}
        />
      )}
    </>
  )
}
