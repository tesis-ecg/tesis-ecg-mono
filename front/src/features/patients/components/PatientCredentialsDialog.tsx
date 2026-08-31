import { KeyRound, TriangleAlert } from 'lucide-react'

import { CopyableSecret } from '@/components/CopyableSecret'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface PatientCredentialsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  patientName: string
  email: string
  password: string
  /** Cambia el copy entre el alta y una regeneración posterior. */
  variant?: 'created' | 'regenerated'
}

/**
 * Muestra la contraseña de la app **la única vez que existe en claro**.
 *
 * Auth0 guarda solo el hash: no hay ningún endpoint que la pueda volver a leer,
 * ni acá ni en la ficha del paciente. Si se pierde, el camino es regenerarla o
 * mandarle el mail de recuperación — nunca "verla". Por eso el diálogo insiste
 * con el aviso y no se cierra al hacer click afuera.
 */
export function PatientCredentialsDialog({
  open,
  onOpenChange,
  patientName,
  email,
  password,
  variant = 'created',
}: PatientCredentialsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        onInteractOutside={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-5 text-primary-500" aria-hidden />
            {variant === 'created' ? 'Acceso a la app creado' : 'Contraseña nueva'}
          </DialogTitle>
          <DialogDescription>
            {variant === 'created'
              ? `${patientName} ya puede entrar a la app con estos datos.`
              : `Pasale a ${patientName} la contraseña nueva. La anterior dejó de funcionar.`}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <span className="text-body3 text-gray-600">Usuario (email o DNI)</span>
            <CopyableSecret value={email} label="Copiar email" />
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-body3 text-gray-600">Contraseña</span>
            <CopyableSecret value={password} label="Copiar contraseña" />
          </div>

          <div className="flex gap-3 rounded-lg border border-warning-300 bg-warning-100 px-4 py-3">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-700" aria-hidden />
            <p className="text-body3 text-warning-700">
              Anotala ahora: no se puede volver a ver. Si se pierde, desde la ficha del paciente
              podés generar una nueva o mandarle un mail para que la cambie él.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Ya la anoté</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
