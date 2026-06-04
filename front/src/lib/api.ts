import axios, { AxiosError } from 'axios'

import { mapAxiosError } from './apiError'
import { attachRetry } from './apiRetry'

const baseURL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

interface AuthHandler {
  onUnauthorized: () => void
}

let handler: AuthHandler = {
  onUnauthorized: () => {},
}

/**
 * Registra el callback que necesita el cliente HTTP para reaccionar a 401.
 * Lo invoca `AuthProvider` al montar.
 */
export function setAuthHandler(next: AuthHandler) {
  handler = next
}

export const api = axios.create({
  baseURL,
  timeout: 15000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

attachRetry(api)

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const apiError = mapAxiosError(error)
    if (apiError.code === 'UNAUTHORIZED') {
      handler.onUnauthorized()
    }
    return Promise.reject(apiError)
  },
)

export { unwrapError, isApiError } from './apiError'
export type { ApiError, ApiErrorCode, ApiResponse } from './apiError'
