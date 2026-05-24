import { AxiosError } from 'axios'

export interface ApiResponse<T> {
  data: T
}

export type ApiErrorCode =
  | 'NETWORK'
  | 'TIMEOUT'
  | 'CANCELLED'
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'VALIDATION'
  | 'CONFLICT'
  | 'SERVER'
  | 'UNKNOWN'

export interface ApiError {
  status: number
  code: ApiErrorCode
  message: string
  fields?: Record<string, string>
  cause?: unknown
}

export function isApiError(err: unknown): err is ApiError {
  return (
    typeof err === 'object' && err !== null && 'code' in err && 'message' in err && 'status' in err
  )
}

export function createApiError(
  partial: Partial<ApiError> & Pick<ApiError, 'code' | 'message'>,
): ApiError {
  return {
    status: partial.status ?? 0,
    code: partial.code,
    message: partial.message,
    fields: partial.fields,
    cause: partial.cause,
  }
}

const STATUS_TO_CODE: Record<number, ApiErrorCode> = {
  400: 'VALIDATION',
  401: 'UNAUTHORIZED',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  422: 'VALIDATION',
}

const FRIENDLY_MESSAGES: Record<ApiErrorCode, string> = {
  NETWORK: 'No pudimos conectarnos al servidor. Revisá tu conexión a internet.',
  TIMEOUT: 'La solicitud tardó demasiado y fue cancelada. Probá de nuevo.',
  CANCELLED: 'La solicitud fue cancelada.',
  UNAUTHORIZED: 'Tu sesión expiró. Iniciá sesión nuevamente.',
  FORBIDDEN: 'No tenés permisos para realizar esta acción.',
  NOT_FOUND: 'El recurso solicitado no existe.',
  VALIDATION: 'Los datos enviados no son válidos.',
  CONFLICT: 'La operación entró en conflicto con el estado actual.',
  SERVER: 'Ocurrió un error en el servidor. Intentá de nuevo más tarde.',
  UNKNOWN: 'Ocurrió un error inesperado.',
}

export function mapAxiosError(err: AxiosError): ApiError {
  if (err.code === 'ECONNABORTED' || err.code === 'ETIMEDOUT') {
    return createApiError({
      status: 0,
      code: 'TIMEOUT',
      message: FRIENDLY_MESSAGES.TIMEOUT,
      cause: err,
    })
  }
  if (err.code === 'ERR_CANCELED') {
    return createApiError({
      status: 0,
      code: 'CANCELLED',
      message: FRIENDLY_MESSAGES.CANCELLED,
      cause: err,
    })
  }
  if (!err.response) {
    return createApiError({
      status: 0,
      code: 'NETWORK',
      message: FRIENDLY_MESSAGES.NETWORK,
      cause: err,
    })
  }

  const status = err.response.status
  const body = err.response.data as
    | { message?: string; detail?: string; fields?: Record<string, string> }
    | undefined

  const code: ApiErrorCode = STATUS_TO_CODE[status] ?? (status >= 500 ? 'SERVER' : 'UNKNOWN')
  const message =
    body?.message ??
    (typeof body?.detail === 'string' ? body.detail : undefined) ??
    FRIENDLY_MESSAGES[code]

  return createApiError({ status, code, message, fields: body?.fields, cause: err })
}

export function unwrapError(err: unknown): string {
  if (isApiError(err)) return err.message
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return FRIENDLY_MESSAGES.UNKNOWN
}
