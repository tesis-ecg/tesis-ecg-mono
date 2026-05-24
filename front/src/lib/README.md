# `src/lib`

Capa de infraestructura compartida entre features.

## `api.ts`

Instancia axios configurada con:

- `baseURL` desde `VITE_API_URL` (default `http://localhost:8000`)
- `timeout` 15s
- Interceptor de request: inyecta `Authorization: Bearer <token>` si el `AuthProvider` registró su getter vía `setAuthHandler`.
- Interceptor de response: normaliza cualquier error a la shape uniforme [`ApiError`](#apierror) y dispara `onUnauthorized` cuando el status es 401 (lo usa el `AuthContext` para forzar logout).
- Retry automático en GETs (network errors + 5xx) con backoff exponencial — ver [`apiRetry.ts`](./apiRetry.ts).

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

## `apiRetry.ts`

Retries automáticos sobre GETs idempotentes:

- Sólo GET (no toca POST/PUT/PATCH/DELETE)
- Network errors y 5xx
- Máximo 2 reintentos por request
- Backoff exponencial: 1s → 2s → 4s (cap 8s)

Para forzar otro límite en una request puntual:

```ts
import { withMaxRetries } from '@/lib/apiRetry'

await api.get('/patients', withMaxRetries({}, 5))
```

## Contrato con `AuthContext`

El `AuthProvider` (en `src/features/auth/AuthProvider.tsx`) invoca `setAuthHandler({ getToken, onUnauthorized })` al montar. Esto evita un ciclo de imports `api.ts ↔ AuthContext` — `api.ts` nunca importa de `auth/`.

Cuando el interceptor mapea un 401, llama al `onUnauthorized` registrado, que dispara logout y limpia localStorage.
