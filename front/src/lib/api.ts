import axios from 'axios'

const baseURL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

interface AuthHandler {
  getToken: () => string | null
  onUnauthorized: () => void
}

let handler: AuthHandler = {
  getToken: () => null,
  onUnauthorized: () => {},
}

/**
 * Registra los callbacks que necesita el cliente HTTP para inyectar el
 * Bearer token y reaccionar a 401. Lo invoca `AuthProvider` al montar para
 * evitar el ciclo de imports api.ts ↔ AuthContext.
 */
export function setAuthHandler(next: AuthHandler) {
  handler = next
}

export const api = axios.create({
  baseURL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = handler.getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      handler.onUnauthorized()
    }
    return Promise.reject(error)
  },
)
