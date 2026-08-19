/// <reference lib="webworker" />

interface DecodeRequest {
  buffer: ArrayBuffer
  expectedBytes: number
  sha256: string | null
}

interface DecodeSuccess {
  ok: true
  samples: ArrayBuffer
}

interface DecodeFailure {
  ok: false
  message: string
}

const workerScope = self as unknown as DedicatedWorkerGlobalScope

workerScope.onmessage = async (event: MessageEvent<DecodeRequest>) => {
  const { buffer, expectedBytes, sha256 } = event.data
  try {
    if (buffer.byteLength !== expectedBytes || buffer.byteLength % 4 !== 0) {
      throw new Error('Los datos del ECG están corruptos o incompletos.')
    }

    if (sha256) {
      const digest = await crypto.subtle.digest('SHA-256', buffer)
      const actual = Array.from(new Uint8Array(digest), (byte) =>
        byte.toString(16).padStart(2, '0'),
      ).join('')
      if (actual !== sha256) throw new Error('Checksum de ECG inválido.')
    }

    const values = new Float32Array(buffer.byteLength / 4)
    const view = new DataView(buffer)
    for (let index = 0; index < values.length; index++) {
      values[index] = view.getFloat32(index * 4, true)
    }
    const response: DecodeSuccess = { ok: true, samples: values.buffer }
    workerScope.postMessage(response, [values.buffer])
  } catch (error) {
    const response: DecodeFailure = {
      ok: false,
      message: error instanceof Error ? error.message : 'No se pudo decodificar el ECG.',
    }
    workerScope.postMessage(response)
  }
}

export {}
