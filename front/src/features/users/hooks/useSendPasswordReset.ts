import { useMutation } from '@tanstack/react-query'

import { sendPasswordReset } from '../api/usersApi'

export function useSendPasswordReset() {
  return useMutation({
    mutationFn: (id: string) => sendPasswordReset(id),
  })
}
