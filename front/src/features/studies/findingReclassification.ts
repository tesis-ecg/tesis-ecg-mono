import type { ReclassificationRequest, StudyFinding } from './types'

type BeatLabel = 'N' | 'S' | 'V'

/**
 * Etiqueta de latido que respalda cada tipo de hallazgo
 * (`back/app/ml/holter_contracts.py::BEAT_LABEL_BY_KIND`). Solo estos tipos se
 * pueden reclasificar a otra etiqueta; el resto admite confirmar, descartar o
 * marcar como ruido.
 */
const BEAT_LABEL_BY_KIND: Record<string, BeatLabel> = {
  ventricular_ectopy: 'V',
  vt_run: 'V',
  supraventricular_ectopy: 'S',
  svt_run: 'S',
  tachycardia: 'N',
}

const BEAT_LABEL_TEXT: Record<BeatLabel, string> = {
  N: 'normales (N)',
  S: 'supraventriculares (S)',
  V: 'ventriculares (V)',
}

/**
 * Hallazgos del motor que son ruido (`NOISE_FINDING_KINDS` en
 * `back/app/ml/holter_contracts.py`): descartarlos devuelve el tramo a las cifras.
 */
const NOISE_FINDING_KINDS = new Set(['noise_burst', 'flatline'])

/** Qué cambia en las cifras al descartar, que depende del tipo (`corrections.py`). */
function rejectHint(kind: string, own: BeatLabel | undefined): string {
  if (own === 'S' || own === 'V') {
    return 'No es lo que el motor dice. Sus latidos pasan a contar como normales (N).'
  }
  if (kind === 'pause') return 'No es una pausa real: deja de contar en las pausas.'
  if (NOISE_FINDING_KINDS.has(kind)) {
    return 'La señal se puede leer: el tramo vuelve a contar en las métricas.'
  }
  return 'No es lo que el motor dice. Sale del informe; las cifras no cambian.'
}

export interface ReclassifyChoice {
  /** Valor único de la opción: la acción, o `relabel:<etiqueta>`. */
  value: string
  label: string
  hint: string
  request: Pick<ReclassificationRequest, 'action' | 'beatLabel'>
}

/** Las acciones que se le ofrecen al médico para este hallazgo. */
export function reclassificationChoices(finding: StudyFinding): ReclassifyChoice[] {
  const own = BEAT_LABEL_BY_KIND[finding.kind]
  const choices: ReclassifyChoice[] = [
    {
      value: 'confirm',
      label: 'Confirmar hallazgo',
      hint: 'Lo revisé y es correcto. Las cifras no cambian.',
      request: { action: 'confirm' },
    },
    {
      value: 'reject',
      label: 'Descartar (falso positivo)',
      hint: rejectHint(finding.kind, own),
      request: { action: 'reject' },
    },
    {
      value: 'noise',
      label: 'Marcar como ruido',
      hint: 'Es artefacto. El tramo sale de todas las métricas, pausas incluidas.',
      request: { action: 'noise' },
    },
  ]
  if (own) {
    for (const label of ['N', 'S', 'V'] as const) {
      if (label === own) continue
      choices.push({
        value: `relabel:${label}`,
        label: `Reclasificar como latidos ${BEAT_LABEL_TEXT[label]}`,
        hint: 'Se re-etiquetan los latidos del tramo y se recalculan las cifras.',
        request: { action: 'relabel', beatLabel: label },
      })
    }
  }
  if (finding.correction) {
    choices.push({
      value: 'reset',
      label: 'Restablecer al análisis automático',
      hint: 'Quita la corrección y vuelve a lo que detectó el motor.',
      request: { action: 'reset' },
    })
  }
  return choices
}

/** Lo decide el backend (`reclassifiable`): lo produjo el motor y tiene un tramo propio. */
export function isReclassifiable(finding: StudyFinding): boolean {
  return finding.reclassifiable === true
}
