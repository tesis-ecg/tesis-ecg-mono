import type { Patient } from '@/features/patients/types'
import type { Study, StudyPatientReport } from '@/features/studies/types'

import type { EcgReportWindow } from './api/ecgApi'
import type { ECGSignal } from './types'

export interface ClinicalReportInput {
  study: Study
  patient: Patient
  signal: ECGSignal
  reports: StudyPatientReport[]
  detailWindows: EcgReportWindow[]
  sectionMinutes: number
  paperSpeed: number
  amplitude: number
  generatedAt: string
}
