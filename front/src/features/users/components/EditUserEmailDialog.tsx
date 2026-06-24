import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { unwrapError } from '@/lib/api'

import { useUpdateUserEmail } from '../hooks/useUpdateUserEmail'
import type { UserAccount } from '../types'
import { userEmailSchema, type UserEmailValues } from '../userSchema'

interface EditUserEmailDialogProps {
  user: UserAccount
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function EditUserEmailDialog({ user, open, onOpenChange }: EditUserEmailDialogProps) {
  const updateEmail = useUpdateUserEmail()

  const form = useForm<UserEmailValues>({
    resolver: zodResolver(userEmailSchema),
    defaultValues: { email: user.email },
  })

  const handleSubmit = (values: UserEmailValues) => {
    if (values.email.trim() === user.email) {
      onOpenChange(false)
      return
    }
    updateEmail.mutate(
      { id: user.id, input: { email: values.email.trim() } },
      {
        onSuccess: () => {
          toast.success('Email actualizado.')
          onOpenChange(false)
        },
        onError: (error) => {
          toast.error(unwrapError(error))
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar email</DialogTitle>
          <DialogDescription>
            Actualizá el email de <strong>{user.fullName}</strong>.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="flex flex-col gap-4">
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input type="email" autoComplete="off" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="flex justify-end pt-2">
              <Button type="submit" disabled={updateEmail.isPending}>
                {updateEmail.isPending ? 'Guardando…' : 'Guardar cambios'}
              </Button>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
