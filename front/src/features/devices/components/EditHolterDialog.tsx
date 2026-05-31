import { zodResolver } from '@hookform/resolvers/zod'
import { Pencil } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
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
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { unwrapError } from '@/lib/api'

import { useUpdateHolter } from '../hooks/useUpdateHolter'
import { holterEditSchema, type HolterEditValues } from '../holterSchema'
import type { Holter, UpdateHolterInput } from '../types'

interface EditHolterDialogProps {
  holter: Holter
  /** Si se pasan `open`/`onOpenChange`, el diálogo se controla externamente y no renderiza su botón disparador. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

/** Devuelve solo los campos cuyo valor cambió respecto del Holter original. */
function diffHolter(holter: Holter, values: HolterEditValues): UpdateHolterInput {
  const firmware = values.firmwareVersion.trim()
  const nextFirmware = firmware === '' ? null : firmware
  const changed: UpdateHolterInput = {}
  if (values.model.trim() !== holter.model) changed.model = values.model.trim()
  if (nextFirmware !== holter.firmwareVersion) changed.firmwareVersion = nextFirmware
  if (
    values.status !== holter.status &&
    // No mandar status si el current es 'assigned' o 'retired' — el form ya lo bloquea.
    (holter.status === 'available' || holter.status === 'maintenance')
  ) {
    changed.status = values.status
  }
  return changed
}

export function EditHolterDialog({ holter, open, onOpenChange }: EditHolterDialogProps) {
  const controlled = open !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const isOpen = controlled ? open : internalOpen
  const setOpen = (next: boolean) => {
    if (controlled) onOpenChange?.(next)
    else setInternalOpen(next)
  }

  const updateHolter = useUpdateHolter()
  const statusLocked = holter.status === 'assigned' || holter.status === 'retired'

  const form = useForm<HolterEditValues>({
    resolver: zodResolver(holterEditSchema),
    defaultValues: {
      model: holter.model,
      firmwareVersion: holter.firmwareVersion ?? '',
      status: holter.status === 'maintenance' ? 'maintenance' : 'available',
    },
  })

  const handleSubmit = (values: HolterEditValues) => {
    const input = diffHolter(holter, values)
    if (Object.keys(input).length === 0) {
      setOpen(false)
      return
    }
    updateHolter.mutate(
      { id: holter.id, input },
      {
        onSuccess: () => {
          toast.success('Holter actualizado.')
          setOpen(false)
        },
        onError: (error) => {
          toast.error(unwrapError(error))
        },
      },
    )
  }

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button variant="outline">
            <Pencil className="mr-1 size-4" aria-hidden />
            Editar
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar Holter</DialogTitle>
          <DialogDescription>
            Modificá los datos del Holter. El serial no se puede cambiar.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="flex flex-col gap-4">
            <FormItem>
              <FormLabel>Serial</FormLabel>
              <FormControl>
                <Input value={holter.serial} readOnly disabled className="font-mono" />
              </FormControl>
              <FormDescription>Identificador único del dispositivo (inmutable).</FormDescription>
            </FormItem>

            <FormField
              control={form.control}
              name="model"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Modelo</FormLabel>
                  <FormControl>
                    <Input placeholder="SIM7080G v1" autoComplete="off" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="firmwareVersion"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Versión de firmware</FormLabel>
                  <FormControl>
                    <Input placeholder="0.3.1" autoComplete="off" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="status"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Estado</FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    value={field.value}
                    disabled={statusLocked}
                  >
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="available">Disponible</SelectItem>
                      <SelectItem value="maintenance">Mantenimiento</SelectItem>
                    </SelectContent>
                  </Select>
                  {statusLocked && (
                    <FormDescription>
                      {holter.status === 'assigned'
                        ? 'Desasigná el Holter antes de cambiar el estado.'
                        : 'El Holter está retirado.'}
                    </FormDescription>
                  )}
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex justify-end pt-2">
              <Button type="submit" disabled={updateHolter.isPending}>
                {updateHolter.isPending ? 'Guardando…' : 'Guardar cambios'}
              </Button>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
