import { ArrowRightLeft, Pencil, Trash2, Unlink } from 'lucide-react'
import { useState } from 'react'

import { TableRowActions, type TableRowAction } from '@/components/ui/table'

import type { Holter } from '../types'
import { DeleteHolterDialog } from './DeleteHolterDialog'
import { EditHolterDialog } from './EditHolterDialog'
import { ReassignHolterDialog } from './ReassignHolterDialog'
import { UnassignHolterDialog } from './UnassignHolterDialog'

export function HolterRowActions({ holter }: { holter: Holter }) {
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [reassignOpen, setReassignOpen] = useState(false)
  const [unassignOpen, setUnassignOpen] = useState(false)

  const isAssigned = holter.assignedPatientId !== null
  const isRetired = holter.status === 'retired'

  // Radix renderiza el contenido del menú y diálogos en un portal, pero los
  // eventos de React burbujean por el árbol de componentes: sin stopPropagation
  // un click dentro del modal dispararía la navegación de la fila.
  const actions: TableRowAction[] = [
    {
      label: 'Editar Holter',
      icon: Pencil,
      onSelect: () => setEditOpen(true),
      disabled: isRetired,
    },
    {
      label: isAssigned ? 'Cambiar paciente' : 'Asignar a paciente',
      icon: ArrowRightLeft,
      onSelect: () => setReassignOpen(true),
      disabled: isRetired,
    },
    ...(isAssigned
      ? [
          {
            label: 'Desasignar',
            icon: Unlink,
            onSelect: () => setUnassignOpen(true),
          } satisfies TableRowAction,
        ]
      : []),
    {
      label: 'Eliminar Holter',
      icon: Trash2,
      variant: 'destructive' as const,
      onSelect: () => setDeleteOpen(true),
      disabled: isRetired,
    },
  ]

  return (
    <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <TableRowActions label={`Acciones de ${holter.serial}`} actions={actions} />
      <EditHolterDialog holter={holter} open={editOpen} onOpenChange={setEditOpen} />
      <DeleteHolterDialog holter={holter} open={deleteOpen} onOpenChange={setDeleteOpen} />
      <ReassignHolterDialog holter={holter} open={reassignOpen} onOpenChange={setReassignOpen} />
      {isAssigned && (
        <UnassignHolterDialog
          holterId={holter.id}
          serial={holter.serial}
          open={unassignOpen}
          onOpenChange={setUnassignOpen}
        />
      )}
    </div>
  )
}
