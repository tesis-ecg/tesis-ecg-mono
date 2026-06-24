import { KeyRound, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { TableRowActions, type TableRowAction } from '@/components/ui/table'
import { unwrapError } from '@/lib/api'

import { useSendPasswordReset } from '../hooks/useSendPasswordReset'
import type { UserAccount } from '../types'
import { DeleteUserDialog } from './DeleteUserDialog'
import { EditUserEmailDialog } from './EditUserEmailDialog'

export function UserRowActions({ user }: { user: UserAccount }) {
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const sendReset = useSendPasswordReset()

  const handleSendReset = () => {
    sendReset.mutate(user.id, {
      onSuccess: () => {
        toast.success(`Email de reseteo enviado a ${user.email}.`)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  const actions: TableRowAction[] = [
    {
      label: 'Editar email',
      icon: Pencil,
      onSelect: () => setEditOpen(true),
    },
    {
      label: 'Enviar reseteo de contraseña',
      icon: KeyRound,
      onSelect: handleSendReset,
      disabled: sendReset.isPending,
    },
    {
      label: 'Eliminar usuario',
      icon: Trash2,
      variant: 'destructive',
      onSelect: () => setDeleteOpen(true),
    },
  ]

  return (
    <>
      <TableRowActions label={`Acciones de ${user.email}`} actions={actions} />
      <EditUserEmailDialog user={user} open={editOpen} onOpenChange={setEditOpen} />
      <DeleteUserDialog user={user} open={deleteOpen} onOpenChange={setDeleteOpen} />
    </>
  )
}
