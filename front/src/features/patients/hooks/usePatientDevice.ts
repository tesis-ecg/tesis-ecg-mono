import { useQuery } from '@tanstack/react-query'

import { getPatientDevice } from '../api/patientsApi'

export function usePatientDevice(id: string | undefined) {
  return useQuery({
    queryKey: ['patients', id, 'device'],
    queryFn: () => getPatientDevice(id!),
    enabled: Boolean(id),
  })
}
