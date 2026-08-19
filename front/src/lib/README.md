# `src/lib`

Capa de infraestructura compartida entre features.

## `api.ts`

Instancia axios configurada con:

- `baseURL` fijo `/api`; Vite y Vercel hacen el proxy al backend correspondiente.
- `timeout` 15s
- Cookie HttpOnly como única credencial; el navegador la adjunta con `withCredentials`.
- Interceptor de response: normaliza cualquier error a la shape uniforme [`ApiError`](#apierror) y dispara una invalidación local single-flight ante 401.
- Los retries viven exclusivamente en TanStack Query: hasta dos intentos totales para GETs; las mutaciones no se reintentan.

### Uso desde un feature

```ts
import { api, unwrapError, type ApiError } from '@/lib/api'

try {
  const { data } = await api.get<Patient[]>('/patients')
  return data
} catch (err) {
  const apiError = err as ApiError
  toast.error(unwrapError(apiError))
  if (apiError.code === 'NOT_FOUND') {
    // render UI específico
  }
  throw apiError
}
```

> Nota: como el interceptor ya mapea a `ApiError`, en el `catch` el error nunca es un `AxiosError`. Tipar como `ApiError` directamente.

## `apiError.ts`

### `ApiError`

```ts
interface ApiError {
  status: number // HTTP status (0 si fue network/timeout)
  code: ApiErrorCode // ver enum abajo
  message: string // mensaje listo para mostrar al usuario (es-AR)
  fields?: Record<string, string> // errores por campo (validación 422)
  serverCode?: string // código estable emitido por el backend
  requestId?: string // correlación con logs del backend
  cause?: unknown // AxiosError original
}

type ApiErrorCode =
  | 'NETWORK' // sin respuesta del servidor
  | 'TIMEOUT' // request abortado por timeout
  | 'CANCELLED' // cancelado por AbortController
  | 'UNAUTHORIZED' // 401
  | 'FORBIDDEN' // 403
  | 'NOT_FOUND' // 404
  | 'VALIDATION' // 400 / 422
  | 'CONFLICT' // 409
  | 'SERVER' // 5xx
  | 'UNKNOWN' // todo lo demás
```

### Helpers

- `unwrapError(err)` — devuelve un string legible. Usar para mostrar mensajes en UI.
- `isApiError(err)` — type guard.
- `mapAxiosError(err)` — usado internamente por el interceptor. No invocar manualmente.

## Contrato con `AuthContext`

El `AuthProvider` (en `src/features/auth/AuthProvider.tsx`) invoca `setAuthHandler({ onUnauthorized })` al montar. Esto evita un ciclo de imports `api.ts ↔ AuthContext` — `api.ts` nunca importa de `auth/`.

Cuando el interceptor mapea un 401, llama al `onUnauthorized` registrado, que cancela requests, vacía TanStack Query y elimina la sesión en memoria. Nunca vuelve a llamar `/logout`, por lo que no puede recursar.
