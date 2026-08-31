import * as SecureStore from 'expo-secure-store'

/**
 * Sesión del paciente en el llavero del sistema.
 *
 * Solo el refresh se persiste. El access vive en memoria y dura una hora: si se
 * guardara también, un token robado del almacenamiento serviría igual, y no se
 * gana nada — al arrancar la app se pide uno nuevo con el refresh.
 */
const REFRESH_KEY = 'holter.refreshToken'

export async function saveRefreshToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(REFRESH_KEY, token, {
    // El paciente puede recibir un aviso con el teléfono en el bolsillo y
    // desbloquearlo para responder; no hace falta que la app funcione con el
    // dispositivo bloqueado.
    keychainAccessible: SecureStore.WHEN_UNLOCKED,
  })
}

export async function readRefreshToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(REFRESH_KEY)
  } catch {
    // Un llavero corrupto o inaccesible no puede dejar la app en un estado del
    // que no se pueda salir: se trata como "no hay sesión" y se pide login.
    return null
  }
}

export async function clearRefreshToken(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(REFRESH_KEY)
  } catch {
    // Nada que hacer: la sesión igual se descarta en memoria.
  }
}
