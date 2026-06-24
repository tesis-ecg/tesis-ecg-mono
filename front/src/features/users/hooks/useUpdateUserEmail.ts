import { useMutation, useQueryClient } from '@tanstack/react-query'

import { updateUserEmail } from '../api/usersApi'
import type { UpdateUserEmailInput } from '../types'

export function useUpdateUserEmail() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateUserEmailInput }) =>
      updateUserEmail(id, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
