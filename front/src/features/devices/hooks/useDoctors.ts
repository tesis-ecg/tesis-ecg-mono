import { useQuery } from '@tanstack/react-query'

import { listDoctors } from '../api/devicesApi'

/** Lista de médicos para asignar dispositivos (solo admin). */
export function useDoctors(enabled = true) {
  return useQuery({
    queryKey: ['doctors'],
    queryFn: listDoctors,
    enabled,
  })
}
