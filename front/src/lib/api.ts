import axios, { AxiosError } from 'axios'

import { mapAxiosError } from './apiError'
const baseURL = '/api'

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
})

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
