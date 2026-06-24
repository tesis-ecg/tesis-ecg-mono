import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { unwrapError } from '@/lib/api'

import { useCreateUser } from '../hooks/useCreateUser'
import type { UserCreateValues } from '../userSchema'
import { UserForm } from './UserForm'

export function NewUserDialog() {
  const [open, setOpen] = useState(false)
  const createUser = useCreateUser()

  const handleSubmit = (values: UserCreateValues) => {
    createUser.mutate(values, {
      onSuccess: (user) => {
        toast.success(`Usuario ${user.email} creado.`)
        setOpen(false)
      },
      onError: (error) => {
        toast.error(unwrapError(error))
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-1 size-4" aria-hidden />
          Agregar usuario
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Agregar usuario</DialogTitle>
          <DialogDescription>
            Creá una cuenta nueva con su email y contraseña. Elegí el rol que tendrá en la
            plataforma.
          </DialogDescription>
        </DialogHeader>
        <UserForm
          onSubmit={handleSubmit}
          isSubmitting={createUser.isPending}
          submitLabel="Crear usuario"
        />
      </DialogContent>
    </Dialog>
  )
}
