import { describe, expect, it } from 'vitest'

import { DETAIL_MAX_VISIBLE_SAMPLES, coversRange, detailRangeFor, mergeDetail } from './traceDetail'

describe('detailRangeFor', () => {
  const base = { plotWidthPx: 1_000, overviewSamplesPerBucket: 64, availableSamples: 1_000_000 }

  it('a 25 mm/s pide las muestras, con media ventana de margen a cada lado', () => {
    // ~10 s a 500 Hz en 1.000 px: 5 muestras por píxel contra baldes de 64.
    const range = detailRangeFor({
      ...base,
      visibleStartSample: 100_000,
      visibleEndSample: 105_000,
    })

    expect(range).toEqual({ startSample: 97_500, endSample: 107_500 })
  })

  it('con un balde por píxel o menos el resumen alcanza', () => {
    const range = detailRangeFor({ ...base, visibleStartSample: 0, visibleEndSample: 64_000 })

    expect(range).toBeNull()
  })

  it('no pide más de lo que se puede dibujar sin trabarse', () => {
    const range = detailRangeFor({
      ...base,
      overviewSamplesPerBucket: 16_384,
      visibleStartSample: 0,
      visibleEndSample: DETAIL_MAX_VISIBLE_SAMPLES + 1,
    })

    expect(range).toBeNull()
  })

  it('el margen se achica para no pasarse del máximo por vista', () => {
    const range = detailRangeFor({
      ...base,
      overviewSamplesPerBucket: 16_384,
      visibleStartSample: 500_000,
      visibleEndSample: 500_000 + DETAIL_MAX_VISIBLE_SAMPLES,
    })

    expect(range!.endSample - range!.startSample).toBe(450_000)
  })

  it('recorta a las muestras que existen', () => {
    const range = detailRangeFor({
      ...base,
      availableSamples: 104_000,
      visibleStartSample: 100_000,
      visibleEndSample: 104_000,
    })

    expect(range).toEqual({ startSample: 98_000, endSample: 104_000 })
  })
})

describe('coversRange', () => {
  it('solo si lo cargado contiene lo pedido', () => {
    const loaded = { startSample: 100, endSample: 200 }
    expect(coversRange(loaded, { startSample: 120, endSample: 180 })).toBe(true)
    expect(coversRange(loaded, { startSample: 90, endSample: 180 })).toBe(false)
    expect(coversRange(null, { startSample: 120, endSample: 180 })).toBe(false)
  })
})

describe('mergeDetail', () => {
  const overviewXs = Float64Array.from([0, 1, 2, 3, 4, 5, 6, 7])
  const overviewYs = Float32Array.from([-1, 1, -1, 1, -1, 1, -1, 1])

  it('reemplaza los puntos del resumen que caen dentro del tramo', () => {
    const merged = mergeDetail(
      overviewXs,
      overviewYs,
      Float64Array.from([2, 2.5, 3, 3.5, 4]),
      Float32Array.from([0.125, 0.25, 0.375, 0.5, 0.625]),
      [],
    )

    expect(Array.from(merged.xs)).toEqual([0, 1, 2, 2.5, 3, 3.5, 4, 5, 6, 7])
    // Corte en cada borde: no se une un mínimo del resumen con una muestra.
    expect(merged.ys).toEqual([-1, 1, null, 0.25, 0.375, 0.5, null, 1, -1, 1])
  })

  it('corta en los huecos de grabación del tramo', () => {
    const merged = mergeDetail(
      overviewXs,
      overviewYs,
      Float64Array.from([2.1, 2.2, 2.3, 5.1, 5.2, 5.3]),
      Float32Array.from([1, 2, 3, 4, 5, 6]),
      [3],
    )

    expect(merged.ys).toEqual([-1, 1, -1, null, 2, 3, null, 5, null, -1, 1])
  })

  it('sin muestras devuelve el resumen', () => {
    const merged = mergeDetail(overviewXs, overviewYs, new Float64Array(), new Float32Array(), [])

    expect(Array.from(merged.xs)).toEqual(Array.from(overviewXs))
  })
})
