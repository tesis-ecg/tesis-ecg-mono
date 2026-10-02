import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
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

import { useCreateHolter } from '../hooks/useCreateHolter'
import type { HolterFormValues } from '../holterSchema'
import { holterFormToInput } from '../utils'
import { HolterForm } from './HolterForm'

export function NewHolterDialog() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const createHolter = useCreateHolter()

  const handleSubmit = (values: HolterFormValues) => {
    createHolter.mutate(holterFormToInput(values), {
      onSuccess: (holter) => {
        toast.success(`Holter ${holter.serial} creado.`)
        setOpen(false)
        // Al detalle del equipo, no de vuelta al listado: lo que sigue después
        // de dar de alta un chaleco es grabarle su API key en el firmware, y
        // `justCreated` hace que la card la muestre sin tener que revelarla.
        navigate(`/devices/${holter.id}`, { state: { justCreated: true } })
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
          Agregar Holter
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Agregar Holter</DialogTitle>
          <DialogDescription>
            Registrá un nuevo Holter en el inventario. Empieza como disponible y te llevamos a su
            ficha, donde está la API key para grabarle el firmware.
          </DialogDescription>
        </DialogHeader>
        <HolterForm
          onSubmit={handleSubmit}
          isSubmitting={createHolter.isPending}
          submitLabel="Agregar Holter"
        />
      </DialogContent>
    </Dialog>
  )
}
