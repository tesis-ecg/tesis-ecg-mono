import { useQuery } from '@tanstack/react-query'

import { listPatients } from '../api/patientsApi'

/**
 * Pacientes sin Holter asignado. Para el Select del dialog de reasignación.
 *
 * El filtro se ejecuta en PostgreSQL para no descargar pacientes innecesarios.
 */
const LIMIT = 250

export function useUnassignedPatients() {
  return useQuery({
    queryKey: ['patients', { unassigned: true }],
    queryFn: () => listPatients({ limit: LIMIT, offset: 0, hasDevice: false }),
  })
}
