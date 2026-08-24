import { useMutation, useQueryClient } from '@tanstack/react-query'

import { invalidateClinicalData } from '@/lib/queryKeys'

import { cancelStudy } from '../api/studiesApi'

export function useCancelStudy() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (studyId: string) => cancelStudy(studyId),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
