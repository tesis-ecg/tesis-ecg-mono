/**
 * La flash del chaleco simulado, persistida en IndexedDB.
 *
 * La flash real sobrevive a un corte de energía. Antes el backlog del simulador
 * vivía solo en memoria: un F5 lo perdía, el backend se quedaba esperando
 * tramas que ya no existían y la única salida era "Reiniciar equipo". Con el
 * backlog persistido, recargar la página se comporta como el equipo real: lo
 * pendiente se retoma y sale en la próxima ventana.
 *
 * No entra en `localStorage`: con la flash llena son 16 MB de binario. Por eso
 * IndexedDB, y por eso con una implementación en memoria de respaldo para los
 * navegadores que lo niegan (Safari privado) y para los tests.
 */

import { FRAME_BYTES } from './codec/frame'
import type { DeviceStorage } from './deviceClock'

export interface FlashStore {
  load(id: string): Promise<DeviceStorage | null>
  save(id: string, sd: DeviceStorage): Promise<void>
  remove(id: string): Promise<void>
}

interface StoredFlash {
  seqs: number[]
  attempts: number[]
  frames: ArrayBuffer
  overflowed: number
}

function serialize(sd: DeviceStorage): StoredFlash {
  const frames = new Uint8Array(sd.pending.length * FRAME_BYTES)
  sd.pending.forEach((frame, i) => frames.set(frame.bytes, i * FRAME_BYTES))
  return {
    seqs: sd.pending.map((frame) => frame.seq),
    attempts: sd.pending.map((frame) => frame.attempts),
    frames: frames.buffer,
    overflowed: sd.overflowed,
  }
}

function deserialize(stored: StoredFlash): DeviceStorage | null {
  const bytes = new Uint8Array(stored.frames)
  if (
    !Array.isArray(stored.seqs) ||
    bytes.length !== stored.seqs.length * FRAME_BYTES ||
    stored.attempts?.length !== stored.seqs.length
  ) {
    return null
  }
  return {
    pending: stored.seqs.map((seq, i) => ({
      seq,
      bytes: bytes.slice(i * FRAME_BYTES, (i + 1) * FRAME_BYTES),
      attempts: stored.attempts[i],
    })),
    overflowed: stored.overflowed ?? 0,
  }
}

export function memoryFlashStore(): FlashStore {
  const store = new Map<string, StoredFlash>()
  return {
    async load(id) {
      const stored = store.get(id)
      return stored ? deserialize(stored) : null
    },
    async save(id, sd) {
      store.set(id, serialize(sd))
    },
    async remove(id) {
      store.delete(id)
    },
  }
}

const DB_NAME = 'holter-vest-sim'
const STORE = 'flash'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(STORE)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function run<T>(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode)
    const request = action(tx.objectStore(STORE))
    tx.oncomplete = () => resolve(request.result as T)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/**
 * IndexedDB con respaldo en memoria. Si abrir la base falla una vez, el resto
 * de la sesión sigue en memoria: el simulador tiene que andar igual, solo que
 * sin sobrevivir al F5.
 */
export function indexedDbFlashStore(): FlashStore {
  const fallback = memoryFlashStore()
  let dbPromise: Promise<IDBDatabase | null> | null = null
  const db = () => {
    dbPromise ??= openDb().catch(() => null)
    return dbPromise
  }
  return {
    async load(id) {
      const handle = await db()
      if (!handle) return fallback.load(id)
      try {
        const stored = await run<StoredFlash | undefined>(handle, 'readonly', (s) => s.get(id))
        return stored ? deserialize(stored) : null
      } catch {
        return fallback.load(id)
      }
    },
    async save(id, sd) {
      const handle = await db()
      if (!handle) return fallback.save(id, sd)
      try {
        await run(handle, 'readwrite', (s) => s.put(serialize(sd), id))
      } catch {
        await fallback.save(id, sd)
      }
    },
    async remove(id) {
      await fallback.remove(id)
      const handle = await db()
      if (!handle) return
      try {
        await run(handle, 'readwrite', (s) => s.delete(id))
      } catch {
        // Nada que hacer: lo peor es una entrada huérfana.
      }
    },
  }
}

export function defaultFlashStore(): FlashStore {
  return typeof indexedDB === 'undefined' ? memoryFlashStore() : indexedDbFlashStore()
}
