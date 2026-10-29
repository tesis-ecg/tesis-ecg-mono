import type { ReclassificationRequest, StudyFinding } from './types'

import { annotationLabel } from '@/features/ecg/annotationMeta'
import type { FindingKind } from '@/generated/openapi'

type BeatLabel = 'N' | 'S' | 'V'

/**
 * Etiqueta de latido que respalda cada tipo de hallazgo
 * (`back/app/ml/holter_contracts.py::BEAT_LABEL_BY_KIND`), usada para explicar
 * el efecto de descartar sobre las métricas.
 */
const BEAT_LABEL_BY_KIND: Record<string, BeatLabel> = {
  ventricular_ectopy: 'V',
  vt_run: 'V',
  supraventricular_ectopy: 'S',
  svt_run: 'S',
  tachycardia: 'N',
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
  return [
    {
      value: 'reject',
      label: 'Descartar',
      hint:
        finding.source === 'manual'
          ? 'Retira este hallazgo manual y su efecto en las métricas.'
          : rejectHint(finding.kind, own),
      request: { action: 'reject' },
    },
    {
      value: 'relabel',
      label: 'Marcar como otro hallazgo',
      hint: 'Elegí el tipo de hallazgo y su gravedad.',
      request: { action: 'relabel' },
    },
  ]
}

/** Lo decide el backend: lo produjo el motor y tiene un tramo propio. */
export function isReclassifiable(finding: StudyFinding): boolean {
  return finding.reclassifiable === true
}

// Record exige que cada tipo admitido por la API tenga una opción en el selector.
const FINDING_KINDS: Record<FindingKind, true> = {
  tachycardia: true,
  bradycardia: true,
  afib: true,
  pvc: true,
  pause: true,
  morphology_anomaly: true,
  recurrent_morphology: true,
  ventricular_ectopy: true,
  supraventricular_ectopy: true,
  svt_run: true,
  vt_run: true,
  av_block_first_degree: true,
  av_block_mobitz_1: true,
  av_block_mobitz_2: true,
  av_block_complete: true,
  wide_qrs_sustained: true,
  short_pr: true,
  noise: true,
  lead_off: true,
  sqi_unanalyzable: true,
  adc_saturated: true,
  flatline: true,
  noise_burst: true,
  normal_beats: true,
  other: true,
}

export const RECLASSIFICATION_KINDS = (Object.keys(FINDING_KINDS) as FindingKind[])
  .map((value) => ({ value, label: annotationLabel(value) }))
  .sort((a, b) => a.label.localeCompare(b.label, 'es'))
