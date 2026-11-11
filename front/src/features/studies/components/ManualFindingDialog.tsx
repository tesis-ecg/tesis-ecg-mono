import { useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
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
import { ANNOTATION_SEVERITY, formatAnnotationDuration } from '@/features/ecg/annotationMeta'
import { formatDate } from '@/features/ecg/clinicalReportFormat'
import type { FindingRange } from '@/features/ecg/measurement'
import type { ECGAnnotationSeverity } from '@/features/ecg/types'
import type { FindingKind } from '@/generated/openapi'
import { unwrapError } from '@/lib/api'

import { RECLASSIFICATION_KINDS } from '../findingReclassification'
import { useCreateManualFinding } from '../hooks/useReclassifyFinding'
import type { StudyFinding } from '../types'

export function ManualFindingDialog({
  studyId,
  range,
  onClose,
  onCreated,
}: {
  studyId: string
  range: FindingRange
  onClose: () => void
  onCreated: (finding: StudyFinding) => void
}) {
  const [kind, setKind] = useState<FindingKind | ''>('')
  const [severity, setSeverity] = useState<ECGAnnotationSeverity | ''>('')
  const request = useRef<{ payload: string; id: string } | null>(null)
  const submitting = useRef(false)
  const mutation = useCreateManualFinding(studyId)
  const submit = () => {
    if (!kind || !severity || submitting.current) return
    submitting.current = true
    const payload = JSON.stringify({ range, kind, severity })
    if (request.current?.payload !== payload) request.current = { payload, id: crypto.randomUUID() }
    mutation.mutate(
      {
        startEpochMs: Math.round(range.startMs),
        endEpochMs: Math.round(range.endMs),
        kind,
        severity,
        clientRequestId: request.current.id,
      },
      {
        onSuccess: (result) => {
          toast.success(
            result.holterMetrics.status === 'ok'
              ? 'Hallazgo guardado. Las métricas se recalcularon.'
              : 'Hallazgo guardado. Las métricas se están recalculando.',
          )
          onCreated(result.finding)
        },
        onError: (error) => toast.error(unwrapError(error)),
        onSettled: () => {
          submitting.current = false
        },
      },
    )
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !submitting.current) onClose()
      }}
    >
      <DialogContent
        showCloseButton={!mutation.isPending}
        onEscapeKeyDown={(event) => {
          event.stopPropagation()
          if (submitting.current) event.preventDefault()
        }}
        onPointerDownOutside={(event) => {
          if (submitting.current) event.preventDefault()
        }}
      >
        <DialogHeader>
          <DialogTitle>Agregar hallazgo manual</DialogTitle>
          <DialogDescription>
            El hallazgo quedará registrado con tu nombre y actualizará las métricas del estudio.
          </DialogDescription>
        </DialogHeader>
        <div className="text-body2 grid gap-1 text-gray-600">
          <span>Inicio: {formatDate(range.startMs)}</span>
          <span>Fin: {formatDate(range.endMs)}</span>
          <span>Duración: {formatAnnotationDuration(range.startMs, range.endMs)}</span>
        </div>
        <div className="grid gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="manual-finding-kind">Tipo de hallazgo</Label>
            <Select
              value={kind}
              onValueChange={(value) => setKind(value as FindingKind)}
              disabled={mutation.isPending}
            >
              <SelectTrigger id="manual-finding-kind" className="w-full">
                <SelectValue placeholder="Seleccionar hallazgo" />
              </SelectTrigger>
              <SelectContent>
                {RECLASSIFICATION_KINDS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="manual-finding-severity">Prioridad</Label>
            <Select
              value={severity}
              onValueChange={(value) => setSeverity(value as ECGAnnotationSeverity)}
              disabled={mutation.isPending}
            >
              <SelectTrigger id="manual-finding-severity" className="w-full">
                <SelectValue placeholder="Seleccionar prioridad" />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(ANNOTATION_SEVERITY).map(([value, meta]) => (
                  <SelectItem key={value} value={value}>
                    {meta.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={mutation.isPending} onClick={onClose}>
            Cancelar
          </Button>
          <Button disabled={!kind || !severity || mutation.isPending} onClick={submit}>
            {mutation.isPending ? 'Guardando…' : 'Guardar hallazgo'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
