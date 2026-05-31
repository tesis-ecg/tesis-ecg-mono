import { useQuery } from '@tanstack/react-query'

import { listPatients } from '../api/patientsApi'

/**
 * Pacientes sin Holter asignado. Para el Select del dialog de reasignación.
 *
 * Mock client-side por ahora: pide la lista completa y filtra por
 * `assignedDeviceId === null`. Cuando el backend exponga `?hasDevice=false` en
 * `GET /patients` (ver TES-17/TES-28), reemplazar este filter por el query param.
 */
const LIMIT = 250

export function useUnassignedPatients() {
  return useQuery({
    queryKey: ['patients', { unassigned: true }],
    queryFn: async () => {
      const result = await listPatients({ limit: LIMIT, offset: 0 })
      return {
        ...result,
        items: result.items.filter((p) => p.assignedDeviceId === null),
      }
    },
  })
}
