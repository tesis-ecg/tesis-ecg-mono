import { AxiosError, isAxiosError, type InternalAxiosRequestConfig } from 'axios'
import { create } from 'axios'
import { Platform } from 'react-native'

/**
 * Cliente HTTP contra el mismo backend que el portal médico, bajo `/mobile`.
 *
 * Dos diferencias con `front/src/lib/api.ts`, y las dos son estructurales:
 *
 * - **Bearer, no cookie.** La sesión del portal vive en `holter_session_v2`,
 *   una cookie `HttpOnly` con `Path=/api` y `SameSite=Lax`. Nada de eso existe
 *   en React Native.
 * - **URL absoluta, no `/api`.** El portal es same-origin detrás de un proxy;
 *   acá el backend está en otro host y hay que nombrarlo.
 */

/** En el emulador de Android, `localhost` es el propio emulador. */
const DEV_FALLBACK = Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://localhost:8000'

export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? DEV_FALLBACK

export const api = create({ baseURL: API_URL, timeout: 20_000 })

type RefreshFn = () => Promise<string | null>

let accessToken: string | null = null
let refreshFn: RefreshFn | null = null
let onSessionExpired: (() => void) | null = null
/** Refresh en vuelo. Sin esto, cinco requests que vencen juntos hacen cinco refresh. */
let pendingRefresh: Promise<string | null> | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function configureSession(options: {
  refresh: RefreshFn
  onSessionExpired: () => void
}): void {
  refreshFn = options.refresh
  onSessionExpired = options.onSessionExpired
}

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.set('Authorization', `Bearer ${accessToken}`)
  }
  return config
})

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean }

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined
    const isAuthCall = config?.url?.startsWith('/mobile/auth/')

    // Un 401 en el propio login o refresh no se reintenta: ahí el 401 *es* la
    // respuesta, y reintentarlo sería un bucle.
    if (error.response?.status !== 401 || !config || config._retried || isAuthCall) {
      throw error
    }

    config._retried = true
    pendingRefresh = pendingRefresh ?? refreshFn?.() ?? Promise.resolve(null)
    let token: string | null = null
    try {
      token = await pendingRefresh
    } finally {
      pendingRefresh = null
    }

    if (!token) {
      onSessionExpired?.()
      throw error
    }
    config.headers.set('Authorization', `Bearer ${token}`)
    return api.request(config)
  },
)

interface ErrorEnvelope {
  code?: string
  message?: string
  fields?: Record<string, string> | null
}

/**
 * Mensaje mostrable de un error del backend.
 *
 * El backend aplana todo a `{code, message, fields}` (ver el handler de
 * `main.py`), así que casi siempre hay un texto en castellano listo para
 * mostrar. Los fallbacks cubren el caso sin red, que en un celular es el más
 * común de todos.
 */
export function unwrapError(error: unknown): string {
  if (isAxiosError<ErrorEnvelope>(error)) {
    const message = error.response?.data?.message
    if (typeof message === 'string' && message) return message
    if (!error.response) return 'No pudimos conectarnos. Revisá tu conexión a internet.'
  }
  return 'Algo no salió bien. Probá de nuevo en un momento.'
}

export function errorCode(error: unknown): string | null {
  if (isAxiosError<ErrorEnvelope>(error)) {
    return error.response?.data?.code ?? null
  }
  return null
}
