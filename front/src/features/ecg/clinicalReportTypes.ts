import type { User } from '@/features/auth/types'
import type {
  StudyClinicalReportSnapshot,
  StudyClinicalReportWindowPlan,
} from '@/features/studies/types'

import type { EcgReportWindow } from './api/ecgApi'

export interface ClinicalReportInput {
  snapshot: StudyClinicalReportSnapshot
  windowPlans: StudyClinicalReportWindowPlan[]
  detailWindows: EcgReportWindow[]
  documentStatus: 'draft' | 'final'
  generatedAt: string
  generatedBy: Pick<User, 'fullName' | 'role'> | null
}
