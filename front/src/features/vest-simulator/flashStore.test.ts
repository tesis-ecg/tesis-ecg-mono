import { describe, expect, it, vi } from 'vitest'

import { FRAME_BYTES } from './codec/frame'
import type { DeviceStorage } from './deviceClock'
import { defaultFlashStore, indexedDbFlashStore, memoryFlashStore } from './flashStore'

function flash(): DeviceStorage {
  return {
    pending: [10, 11, 12].map((seq) => ({
      seq,
      bytes: new Uint8Array(FRAME_BYTES).fill(seq),
      attempts: seq - 10,
    })),
    overflowed: 16,
  }
}

describe('flash persistida', () => {
  it('ida y vuelta: mismas tramas, mismos intentos, misma pérdida', async () => {
    const store = memoryFlashStore()
    await store.save('vest-1', flash())

    const restored = await store.load('vest-1')

    expect(restored?.pending.map((f) => f.seq)).toEqual([10, 11, 12])
    expect(restored?.pending.map((f) => f.attempts)).toEqual([0, 1, 2])
    expect(restored?.pending[2].bytes).toEqual(new Uint8Array(FRAME_BYTES).fill(12))
    expect(restored?.overflowed).toBe(16)
  })

  it('lo restaurado no comparte memoria con lo guardado', async () => {
    const store = memoryFlashStore()
    const original = flash()
    await store.save('vest-1', original)
    original.pending[0].bytes[0] = 99

    expect((await store.load('vest-1'))?.pending[0].bytes[0]).toBe(10)
  })

  it('borrar el chaleco borra su flash', async () => {
    const store = memoryFlashStore()
    await store.save('vest-1', flash())
    await store.remove('vest-1')

    expect(await store.load('vest-1')).toBeNull()
  })

  it('sin IndexedDB cae a memoria y sigue andando', async () => {
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('denegado')
      },
    })
    try {
      const store = indexedDbFlashStore()
      await store.save('vest-1', flash())
      expect((await store.load('vest-1'))?.pending).toHaveLength(3)
    } finally {
      vi.unstubAllGlobals()
    }
    expect(await defaultFlashStore().load('nada')).toBeNull()
  })
})
