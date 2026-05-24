import type {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  InternalAxiosRequestConfig,
} from 'axios'

interface RetryConfig extends InternalAxiosRequestConfig {
  __retryCount?: number
  __maxRetries?: number
}

const DEFAULT_MAX_RETRIES = 2

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

function shouldRetry(error: AxiosError, attempt: number, max: number): boolean {
  if (attempt >= max) return false
  if (error.config?.method && error.config.method.toLowerCase() !== 'get') return false
  if (error.code === 'ERR_CANCELED') return false
  if (!error.response) return true
  return error.response.status >= 500 && error.response.status <= 599
}

export function attachRetry(instance: AxiosInstance, max = DEFAULT_MAX_RETRIES): void {
  instance.interceptors.response.use(
    (response) => response,
    async (error: AxiosError) => {
      const config = error.config as RetryConfig | undefined
      if (!config) return Promise.reject(error)

      const attempt = config.__retryCount ?? 0
      const limit = config.__maxRetries ?? max
      if (!shouldRetry(error, attempt, limit)) {
        return Promise.reject(error)
      }

      config.__retryCount = attempt + 1
      const backoffMs = Math.min(1000 * 2 ** attempt, 8000)
      await sleep(backoffMs)
      return instance.request(config)
    },
  )
}

export function withMaxRetries<T extends AxiosRequestConfig>(
  config: T,
  max: number,
): T & { __maxRetries: number } {
  return { ...config, __maxRetries: max }
}
