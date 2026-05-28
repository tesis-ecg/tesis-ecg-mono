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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { unwrapError } from '@/lib/api'

import { useDeletePatient } from '../hooks/useDeletePatient'
import type { Patient } from '../types'

interface DeletePatientDialogProps {
  patient: Patient
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function DeletePatientDialog({ patient, open, onOpenChange }: DeletePatientDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const [confirmDni, setConfirmDni] = useState('')
  const navigate = useNavigate()
  const deletePatient = useDeletePatient()

  const canDelete = confirmDni.trim() === patient.dni

  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
  }

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) setConfirmDni('')
  }

  const handleDelete = () => {
    if (!canDelete) return
    deletePatient.mutate(patient.id, {
      onSuccess: () => {
        toast.success(`Paciente ${patient.fullName} eliminado.`)
        setOpen(false)
        navigate('/patients')
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
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
          <DialogTitle>Eliminar paciente</DialogTitle>
          <DialogDescription>
            Esta acción es permanente. Se eliminará a <strong>{patient.fullName}</strong> y sus
            datos asociados. Para confirmar, escribí el DNI del paciente ({patient.dni}).
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <Label htmlFor="confirm-dni">DNI del paciente</Label>
          <Input
            id="confirm-dni"
            value={confirmDni}
            onChange={(e) => setConfirmDni(e.target.value)}
            placeholder={patient.dni}
            inputMode="numeric"
            autoComplete="off"
          />
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={!canDelete || deletePatient.isPending}
          >
            {deletePatient.isPending ? 'Eliminando…' : 'Eliminar paciente'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
