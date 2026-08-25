/**
 * Fixture dorado: el único test que prueba que el codec del simulador respeta
 * el formato del **firmware**, y no solo que se entiende consigo mismo.
 *
 * De este lado se fija que el encoder produzca bytes estables. Del otro,
 * `back/tests/test_ecg_decompression.py` decodifica exactamente el mismo
 * archivo con el port del decodificador NORMATIVO de `EcgFrameCodec.h` y exige
 * reproducir estas muestras y estos flags sin una sola diferencia.
 *
 * Si el encoder se desvía del formato, uno de los dos lados falla.
 *
 * Para regenerarlo después de un cambio deliberado del codec:
 *
 *     UPDATE_GOLDEN=1 npx vitest run src/features/vest-simulator/codec/goldenFrames.test.ts
 *
 * y volver a correr `pytest tests/test_ecg_decompression.py` en `back/`.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { FRAME_BYTES, readHeader } from './frame'
import { GOLDEN_BOOT_ID, GOLDEN_CONFIG, GOLDEN_FIRST_SEQ, buildGoldenFixture } from './goldenFrames'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '../../../../../back/tests/fixtures')
const BIN = join(FIXTURES, 'frames_golden.bin')
const META = join(FIXTURES, 'frames_golden.json')

describe('fixture dorado', () => {
  const fixture = buildGoldenFixture()

  if (process.env.UPDATE_GOLDEN) {
    it('regenera el fixture', () => {
      mkdirSync(FIXTURES, { recursive: true })
      writeFileSync(BIN, fixture.binary)
      writeFileSync(
        META,
        `${JSON.stringify(
          {
            note:
              'Generado por front/src/features/vest-simulator/codec/goldenFrames.test.ts ' +
              'con UPDATE_GOLDEN=1. Lo decodifica back/tests/test_ecg_decompression.py. ' +
              'NO editar a mano.',
            config: GOLDEN_CONFIG,
            firstSeq: GOLDEN_FIRST_SEQ,
            bootId: GOLDEN_BOOT_ID,
            simulated: true,
            frameCount: fixture.frames.length,
            sampleCount: fixture.rawUV.length,
            rawUV: fixture.rawUV,
            flags: fixture.flags,
          },
          null,
          1,
        )}\n`,
      )
      expect(existsSync(BIN)).toBe(true)
    })
    return
  }

  it('el encoder sigue produciendo exactamente los bytes commiteados', () => {
    expect(existsSync(BIN), 'falta el fixture: correr con UPDATE_GOLDEN=1 para generarlo').toBe(
      true,
    )

    expect(Buffer.from(fixture.binary).equals(readFileSync(BIN))).toBe(true)
  })

  it('las muestras y los flags esperados coinciden con el metadata', () => {
    const meta = JSON.parse(readFileSync(META, 'utf8'))

    expect(fixture.rawUV).toEqual(meta.rawUV)
    expect(fixture.flags).toEqual(meta.flags)
    expect(fixture.frames.length).toBe(meta.frameCount)
  })

  it('ejercita los caminos que importan del formato', () => {
    const headers = fixture.frames.map(readHeader)

    expect(fixture.binary.length % FRAME_BYTES).toBe(0)
    expect(headers[0].seq).toBe(GOLDEN_FIRST_SEQ)
    expect(headers.every((h) => h.bootId === GOLDEN_BOOT_ID)).toBe(true)
    expect(headers.every((h) => h.simulated)).toBe(true)
    // Varias corridas de flags: si fuera una sola, el RLE no se estaría probando.
    expect(Math.max(...headers.map((h) => h.runCount))).toBeGreaterThan(1)
    // Offset de continua de 120 mV: fuera del rango de un int16 en µV.
    expect(Math.max(...fixture.rawUV)).toBeGreaterThan(32_767)
  })
})
