import { useMutation, useQueryClient } from '@tanstack/react-query'

import { createUser } from '../api/usersApi'
import type { CreateUserInput } from '../types'

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateUserInput) => createUser(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
