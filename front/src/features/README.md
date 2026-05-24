# `src/features`

Cada subdirectorio representa un dominio de la app (auth, patients, devices, studies, etc.) y agrupa sus tipos, mocks, llamadas API y hooks.

```
src/features/<feature>/
├── api.ts                  # funciones API (devuelven Promesas tipadas)
├── api/<feature>Api.ts     # alternativa cuando hay varias funciones
├── components/             # componentes UI específicos del dominio
├── hooks/                  # hooks que envuelven api.ts con TanStack Query
├── mocks.ts                # datos mock + helpers (mientras no haya backend)
└── types.ts                # tipos del dominio
```

## TanStack Query — Convención de query keys

```
['<feature>']                        // lista raíz, ej. ['patients']
['<feature>', id]                    // detalle, ej. ['patients', '42']
['<feature>', id, '<sub>']           // sub-recurso, ej. ['patients', '42', 'studies']
['<feature>', { ...params }]         // lista con params (search, filters), ej. ['patients', { q: 'garcia', status: ['active'] }]
```

Reglas:

- El primer elemento siempre es el nombre del feature en plural (ej. `'patients'`).
- Sub-recursos van como string literal después del id (`'studies'`, `'device'`).
- Params (search, paginación, filtros) van como objeto al final.
- **Invalidación**: invalidar `['patients']` invalida todas las queries derivadas (lista + detalles + sub-recursos).

## Patrón: hook por recurso/acción

```ts
// src/features/patients/hooks/usePatients.ts
export function usePatients(params: PatientListParams) {
  return useQuery({
    queryKey: ['patients', params],
    queryFn: () => listPatients(params),
  })
}

// src/features/patients/hooks/usePatient.ts
export function usePatient(id: string) {
  return useQuery({
    queryKey: ['patients', id],
    queryFn: () => getPatient(id),
    enabled: Boolean(id),
  })
}

// src/features/patients/hooks/useUpdatePatient.ts
export function useUpdatePatient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: updatePatient,
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['patients'] })
      queryClient.invalidateQueries({ queryKey: ['patients', id] })
    },
  })
}
```

## Mocks — estado actual

Mientras el backend no exista (ver tickets TES-16, TES-17, TES-18, TES-19), cada función en `api.ts` tiene la llamada real **comentada** con un `TODO(TES-XX backend)` y un return mock. Para activar el backend:

1. Descomentar la llamada `api.get(...)` / `api.post(...)`.
2. Borrar las líneas marcadas como `// MOCK`.
3. Borrar el import de `./mocks`.

Los datos mock viven en `<feature>/mocks.ts`.
