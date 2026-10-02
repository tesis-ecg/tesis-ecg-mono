import { KeyRound, Mail, RefreshCw } from 'lucide-react'

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

export type AppAccessAction = 'create' | 'regenerate' | 'reset'

interface ConfirmAppAccessDialogProps {
  action: AppAccessAction
  patientName: string
  /** Destinatario del mail de recuperación. Solo se usa en `reset`. */
  contactEmail: string | null
  isPending: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

/**
 * Confirmación de las acciones de acceso a la app.
 *
 * Las tres tocan la credencial con la que el paciente entra desde su celular, y
 * dos de ellas no se pueden deshacer desde la UI: regenerar deja afuera al que
 * está usando la app en ese momento, y el mail sale disparado apenas se
 * confirma. Antes eran tres botones que mutaban al primer click.
 */
export function ConfirmAppAccessDialog({
  action,
  patientName,
  contactEmail,
  isPending,
  open,
  onOpenChange,
  onConfirm,
}: ConfirmAppAccessDialogProps) {
  const copy = COPY[action]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.description(patientName, contactEmail)}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Volver</Button>
          </DialogClose>
          <Button onClick={onConfirm} disabled={isPending}>
            <copy.icon className="mr-1 size-4" aria-hidden />
            {isPending ? copy.pendingLabel : copy.confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface ActionCopy {
  title: string
  description: (patientName: string, contactEmail: string | null) => React.ReactNode
  confirmLabel: string
  pendingLabel: string
  icon: typeof KeyRound
}

const COPY: Record<AppAccessAction, ActionCopy> = {
  create: {
    title: 'Crear acceso a la app',
    description: (patientName) => (
      <>
        Se le crea la cuenta a <strong>{patientName}</strong> y se genera una contraseña que{' '}
        <strong>vas a ver una sola vez</strong>: anotala o dictásela antes de cerrar el cartel.
        Después va a poder entrar con su email o su DNI.
      </>
    ),
    confirmLabel: 'Crear acceso',
    pendingLabel: 'Creando…',
    icon: KeyRound,
  },
  regenerate: {
    title: 'Regenerar contraseña',
    description: (patientName) => (
      <>
        La contraseña actual de <strong>{patientName}</strong> deja de funcionar en el acto. Si está
        usando la app, queda afuera hasta que le dictes la nueva —que también se muestra una sola
        vez—. Si no lo tenés a mano, conviene mandarle el mail de recuperación.
      </>
    ),
    confirmLabel: 'Regenerar contraseña',
    pendingLabel: 'Generando…',
    icon: RefreshCw,
  },
  reset: {
    title: 'Enviar mail de recuperación',
    description: (patientName, contactEmail) => (
      <>
        Le mandamos un mail a <strong>{contactEmail ?? 'su dirección de contacto'}</strong> para que{' '}
        <strong>{patientName}</strong> elija una contraseña nueva. La actual le sigue sirviendo
        hasta que la cambie.
      </>
    ),
    confirmLabel: 'Enviar mail',
    pendingLabel: 'Enviando…',
    icon: Mail,
  },
}
