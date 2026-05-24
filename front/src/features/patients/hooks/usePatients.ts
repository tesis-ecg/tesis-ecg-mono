import { useQuery } from '@tanstack/react-query'

import { listPatients } from '../api/patientsApi'
import type { PatientListParams } from '../types'

export function usePatients(params: PatientListParams) {
  return useQuery({
    queryKey: ['patients', params],
    queryFn: () => listPatients(params),
  })
}
