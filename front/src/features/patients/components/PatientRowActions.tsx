import { Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { TableRowActions } from '@/components/ui/table'

import type { Patient } from '../types'
import { DeletePatientDialog } from './DeletePatientDialog'
import { EditPatientDialog } from './EditPatientDialog'

export function PatientRowActions({ patient }: { patient: Patient }) {
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  // Radix renderiza el contenido de menú y diálogos en un portal, pero los
  // eventos de React burbujean por el árbol de componentes (no por el DOM):
  // sin frenar la propagación acá, un click dentro del modal dispararía el
  // onClick de la fila (navegación al detalle).
  return (
    <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <TableRowActions
        label={`Acciones de ${patient.fullName}`}
        actions={[
          { label: 'Editar paciente', icon: Pencil, onSelect: () => setEditOpen(true) },
          {
            label: 'Eliminar paciente',
            icon: Trash2,
            variant: 'destructive',
            onSelect: () => setDeleteOpen(true),
          },
        ]}
      />
      <EditPatientDialog patient={patient} open={editOpen} onOpenChange={setEditOpen} />
      <DeletePatientDialog patient={patient} open={deleteOpen} onOpenChange={setDeleteOpen} />
    </div>
  )
}
