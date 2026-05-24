import { useQuery } from '@tanstack/react-query'

import { getPatient } from '../api/patientsApi'

export function usePatient(id: string | undefined) {
  return useQuery({
    queryKey: ['patients', id],
    queryFn: () => getPatient(id!),
    enabled: Boolean(id),
  })
}
