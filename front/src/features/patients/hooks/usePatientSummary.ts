import { useQuery } from '@tanstack/react-query'

import { getPatientSummary } from '../api/patientsApi'

export function usePatientSummary(id: string | undefined) {
  return useQuery({
    queryKey: ['patients', id, 'summary'],
    queryFn: () => getPatientSummary(id!),
    enabled: Boolean(id),
  })
}
