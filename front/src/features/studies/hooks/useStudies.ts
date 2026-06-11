import { useQuery } from '@tanstack/react-query'

import { listStudies } from '../api/studiesApi'
import type { StudyListParams } from '../types'

export function useStudies(params: StudyListParams) {
  return useQuery({
    queryKey: ['studies', params],
    queryFn: () => listStudies(params),
  })
}
