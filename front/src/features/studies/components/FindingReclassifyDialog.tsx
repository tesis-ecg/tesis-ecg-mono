import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
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
import { Textarea } from '@/components/ui/textarea'
import { annotationLabel } from '@/features/ecg/annotationMeta'
import { formatDate } from '@/features/ecg/clinicalReportFormat'
import { unwrapError } from '@/lib/api'
import { cn } from '@/lib/utils'

import { useReclassifyFinding } from '../hooks/useReclassifyFinding'
import { reclassificationChoices } from '../findingReclassification'
import type { StudyFinding, StudyFindingCorrection } from '../types'

const CORRECTION_TEXT: Record<StudyFindingCorrection['action'], string> = {
  confirm: 'Confirmado',
  reject: 'Descartado',
  noise: 'Marcado como ruido',
  relabel: 'Reclasificado',
}

/**
 * El estado de corrección de un hallazgo: qué se hizo, el tipo original, quién
 * y cuándo. El original se muestra siempre — la corrección no lo reemplaza.
 */
export function FindingCorrectionStatus({ finding }: { finding: StudyFinding }) {
  const correction = finding.correction
  if (!correction) return null
  const effective =
    correction.action === 'relabel' && correction.effectiveKind
      ? ` a ${annotationLabel(correction.effectiveKind)}`
      : ''
  const by = correction.correctedByName ?? 'un usuario'
  const when = correction.correctedAt ? ` el ${formatDate(correction.correctedAt)}` : ''
  return (
    <span className="text-body3 mt-1 flex flex-col gap-0.5 text-gray-600" data-correction-status>
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge variant={correction.action === 'confirm' ? 'success' : 'info'}>
          {CORRECTION_TEXT[correction.action]}
          {effective}
        </Badge>
        <span>
          por {by}
          {when}
        </span>
      </span>
      <span className="text-gray-500">Original: {annotationLabel(finding.kind)}</span>
      {correction.note && <span className="text-gray-500">Nota: {correction.note}</span>}
    </span>
  )
}

interface FindingReclassifyDialogProps {
  studyId: string
  finding: StudyFinding
  open: boolean
  onOpenChange: (open: boolean) => void
}

const NOTE_MAX = 1024

export function FindingReclassifyDialog({
  studyId,
  finding,
  open,
  onOpenChange,
}: FindingReclassifyDialogProps) {
  const choices = reclassificationChoices(finding)
  const [selected, setSelected] = useState<string>('')
  const [note, setNote] = useState('')
  const mutation = useReclassifyFinding(studyId)

  const choice = choices.find((item) => item.value === selected)

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setSelected('')
      setNote('')
    }
    onOpenChange(next)
  }

  const handleSubmit = () => {
    if (!choice) return
    const trimmed = note.trim()
    mutation.mutate(
      {
        findingId: finding.id,
        body: { ...choice.request, note: trimmed ? trimmed : null },
      },
      {
        onSuccess: (result) => {
          toast.success(
            result.holterMetrics.status === 'ok'
              ? 'Corrección guardada. Las métricas se recalcularon.'
              : 'Corrección guardada. Las métricas se están recalculando.',
          )
          handleOpenChange(false)
        },
        onError: (error) => toast.error(unwrapError(error)),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reclasificar hallazgo</DialogTitle>
          <DialogDescription>
            {annotationLabel(finding.kind)} del {formatDate(finding.startEpochMs)}. El hallazgo
            original se conserva; la corrección queda registrada con tu nombre y la hora, y las
            métricas del informe se recalculan. Los informes ya finalizados no cambian.
          </DialogDescription>
        </DialogHeader>

        <fieldset className="flex flex-col gap-1.5">
          <legend className="sr-only">Qué hacer con el hallazgo</legend>
          {choices.map((item) => (
            <Label
              key={item.value}
              htmlFor={`reclassify-${item.value}`}
              className={cn(
                'cursor-pointer items-start gap-3 rounded-md border border-border p-3 font-normal transition-colors hover:bg-gray-50',
                selected === item.value && 'border-primary bg-primary-50',
              )}
            >
              <input
                id={`reclassify-${item.value}`}
                type="radio"
                name="reclassify-action"
                value={item.value}
                checked={selected === item.value}
                onChange={() => setSelected(item.value)}
                className="mt-0.5 accent-primary"
              />
              <span className="flex flex-col gap-0.5">
                <span className="text-body2 font-medium text-gray-900">{item.label}</span>
                <span className="text-body3 text-gray-600">{item.hint}</span>
              </span>
            </Label>
          ))}
        </fieldset>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="reclassify-note">Nota (opcional)</Label>
          <Textarea
            id="reclassify-note"
            value={note}
            maxLength={NOTE_MAX}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Por qué se corrige"
          />
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Volver</Button>
          </DialogClose>
          <Button onClick={handleSubmit} disabled={!choice || mutation.isPending}>
            {mutation.isPending ? 'Guardando…' : 'Guardar corrección'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
