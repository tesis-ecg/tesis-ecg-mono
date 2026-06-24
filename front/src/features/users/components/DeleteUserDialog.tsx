import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { unwrapError } from '@/lib/api'

import { useDeleteUser } from '../hooks/useDeleteUser'
import type { UserAccount } from '../types'

interface DeleteUserDialogProps {
  user: UserAccount
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function DeleteUserDialog({ user, open, onOpenChange }: DeleteUserDialogProps) {
  const deleteUser = useDeleteUser()

  const handleDelete = () => {
    deleteUser.mutate(user.id, {
      onSuccess: () => {
        toast.success(`Usuario ${user.email} eliminado.`)
        onOpenChange(false)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Eliminar usuario</DialogTitle>
          <DialogDescription>
            La cuenta de <strong>{user.fullName}</strong> ({user.email}) se eliminará y perderá el
            acceso a la plataforma. Esta acción no se puede deshacer.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancelar</Button>
          </DialogClose>
          <Button variant="destructive" onClick={handleDelete} disabled={deleteUser.isPending}>
            {deleteUser.isPending ? 'Eliminando…' : 'Eliminar usuario'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
