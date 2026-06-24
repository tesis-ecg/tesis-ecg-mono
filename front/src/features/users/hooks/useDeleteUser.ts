import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deleteUser } from '../api/usersApi'

export function useDeleteUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteUser(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
