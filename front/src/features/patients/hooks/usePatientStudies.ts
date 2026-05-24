import { useQuery } from '@tanstack/react-query'

import { getPatientStudies } from '../api/patientsApi'

export function usePatientStudies(id: string | undefined) {
  return useQuery({
    queryKey: ['patients', id, 'studies'],
    queryFn: () => getPatientStudies(id!),
    enabled: Boolean(id),
  })
}
