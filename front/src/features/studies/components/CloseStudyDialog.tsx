import { CheckCircle2, XCircle } from 'lucide-react'
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
import { unwrapError } from '@/lib/api'

import { useCancelStudy } from '../hooks/useCancelStudy'
import { useCompleteStudy } from '../hooks/useCompleteStudy'

type CloseMode = 'complete' | 'cancel'

interface CloseStudyDialogProps {
  studyId: string
  patientName: string
  mode: CloseMode
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Confirmación para terminar un estudio.
 *
 * Las dos salidas son irreversibles y no dicen lo mismo: **finalizar** archiva
 * un registro clínico válido; **cancelar** lo descarta. Comparten el diálogo
 * pero no el texto, porque elegir mal la opción no se puede deshacer desde la
 * UI.
 */
export function CloseStudyDialog({
  studyId,
  patientName,
  mode,
  open,
  onOpenChange,
}: CloseStudyDialogProps) {
  const complete = useCompleteStudy()
  const cancel = useCancelStudy()
  const mutation = mode === 'complete' ? complete : cancel

  const handleConfirm = () => {
    mutation.mutate(studyId, {
      onSuccess: () => {
        toast.success(
          mode === 'complete'
            ? `Estudio de ${patientName} finalizado.`
            : `Estudio de ${patientName} cancelado.`,
        )
        onOpenChange(false)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {mode === 'complete' ? 'Finalizar estudio' : 'Cancelar estudio'}
          </DialogTitle>
          <DialogDescription>
            {mode === 'complete' ? (
              <>
                El estudio de <strong>{patientName}</strong> queda cerrado con la hora actual como
                fin. El Holter deja de sumarle señal: los lotes que llegue a mandar después abren un
                estudio nuevo. No se puede reabrir.
              </>
            ) : (
              <>
                El estudio de <strong>{patientName}</strong> se descarta. Usá esto cuando la
                colocación falló o los datos no son del paciente. La señal ya recibida se conserva,
                pero el estudio deja de contar como registro clínico. No se puede reabrir.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Volver</Button>
          </DialogClose>
          <Button
            onClick={handleConfirm}
            disabled={mutation.isPending}
            variant={mode === 'complete' ? 'default' : 'destructive'}
          >
            {mode === 'complete' ? (
              <CheckCircle2 className="mr-1 size-4" aria-hidden />
            ) : (
              <XCircle className="mr-1 size-4" aria-hidden />
            )}
            {mutation.isPending
              ? 'Guardando…'
              : mode === 'complete'
                ? 'Finalizar estudio'
                : 'Cancelar estudio'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
