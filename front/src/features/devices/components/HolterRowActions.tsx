import { ArrowRightLeft, Pencil, Stethoscope, Trash2, Unlink } from 'lucide-react'
import { useState } from 'react'

import { TableRowActions, type TableRowAction } from '@/components/ui/table'
import { useAuth } from '@/features/auth/AuthContext'

import type { Holter } from '../types'
import { AssignDeviceToDoctorDialog } from './AssignDeviceToDoctorDialog'
import { DeleteHolterDialog } from './DeleteHolterDialog'
import { EditHolterDialog } from './EditHolterDialog'
import { ReassignHolterDialog } from './ReassignHolterDialog'
import { UnassignHolterDialog } from './UnassignHolterDialog'

export function HolterRowActions({ holter }: { holter: Holter }) {
  const { user } = useAuth()
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [assignDoctorOpen, setAssignDoctorOpen] = useState(false)
  const [reassignOpen, setReassignOpen] = useState(false)
  const [unassignOpen, setUnassignOpen] = useState(false)

  const isAdmin = user?.role === 'admin'
  const isMedico = user?.role === 'medico'
  const isAssigned = holter.assignedPatientId !== null
  const isRetired = holter.status === 'retired'

  // Admin gestiona el inventario (editar / asignar a médico / eliminar).
  // Médico asigna sus dispositivos a sus pacientes. El resto solo visualiza.
  const actions: TableRowAction[] = isAdmin
    ? [
        {
          label: 'Editar Holter',
          icon: Pencil,
          onSelect: () => setEditOpen(true),
          disabled: isRetired,
        },
        {
          label: 'Asignar a médico',
          icon: Stethoscope,
          onSelect: () => setAssignDoctorOpen(true),
          disabled: isRetired,
        },
        {
          label: 'Eliminar Holter',
          icon: Trash2,
          variant: 'destructive' as const,
          onSelect: () => setDeleteOpen(true),
          disabled: isRetired,
        },
      ]
    : isMedico
      ? [
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
        ]
      : []

  // Cualquier rol no reconocido queda sin acciones (fail-closed).
  if (actions.length === 0) return null

  return (
    <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <TableRowActions label={`Acciones de ${holter.serial}`} actions={actions} />
      {isAdmin && (
        <>
          <EditHolterDialog holter={holter} open={editOpen} onOpenChange={setEditOpen} />
          <DeleteHolterDialog holter={holter} open={deleteOpen} onOpenChange={setDeleteOpen} />
          <AssignDeviceToDoctorDialog
            holter={holter}
            open={assignDoctorOpen}
            onOpenChange={setAssignDoctorOpen}
          />
        </>
      )}
      {isMedico && (
        <>
          <ReassignHolterDialog
            holter={holter}
            open={reassignOpen}
            onOpenChange={setReassignOpen}
          />
          {isAssigned && (
            <UnassignHolterDialog
              holterId={holter.id}
              serial={holter.serial}
              open={unassignOpen}
              onOpenChange={setUnassignOpen}
            />
          )}
        </>
      )}
    </div>
  )
}
