import { useQuery } from '@tanstack/react-query'

import { getStudy } from '../api/studiesApi'

export function useStudy(id: string | undefined) {
  return useQuery({
    queryKey: ['studies', id],
    queryFn: () => getStudy(id!),
    enabled: Boolean(id),
  })
}
