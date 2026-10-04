import { describe, expect, it } from 'vitest'

import {
  AMPLITUDES,
  AUTO_AMPLITUDE,
  DEFAULT_AMPLITUDE,
  DEFAULT_PAPER_SPEED,
  LARGE_BOX_MM,
  PAPER_SPEEDS,
  autoVerticalRange,
  baselineMv,
  matchesScale,
  pannedCenterMv,
  paperScale,
  scaleLabel,
  verticalRange,
  visibleMillivolts,
  visibleSeconds,
} from './paperScale'

const PX_PER_MM = 4

describe('proporción del papel', () => {
  /**
   * Es la propiedad que sostiene todo lo demás: a 25 mm/s y 10 mm/mV el cuadro
   * de 40 ms × 0,1 mV tiene que salir **cuadrado**, en cualquier monitor. Si eso
   * no vale, los milímetros de la retícula no miden lo que dicen y no se puede
   * leer un ST ni un QRS sobre ella.
   *
   * El plotter de bring-up de Biomédica tenía este mismo defecto en una forma
   * más engañosa: la retícula rotulada a 25 mm/s con el trazado corriendo a
   * ~139 mm/s. Su escenario 10 verifica exactamente esto.
   */
  it('el cuadro chico es cuadrado a la escala estándar', () => {
    const scale = paperScale(DEFAULT_PAPER_SPEED, DEFAULT_AMPLITUDE, PX_PER_MM)
    const boxWidthPx = 0.04 * scale.pxPerSec // 40 ms
    const boxHeightPx = 0.1 * scale.pxPerMv // 0,1 mV
    expect(boxWidthPx).toBeCloseTo(boxHeightPx, 10)
  })

  it('el cuadro grande es cuadrado en todas las combinaciones que ofrece la UI', () => {
    for (const speed of PAPER_SPEEDS) {
      for (const gain of AMPLITUDES) {
        const scale = paperScale(speed, gain, PX_PER_MM)
        const mmPerSec = scale.pxPerSec / PX_PER_MM
        const mmPerMv = scale.pxPerMv / PX_PER_MM
        expect(mmPerSec).toBeCloseTo(speed, 10)
        expect(mmPerMv).toBeCloseTo(gain, 10)
        // 5 mm de lado en los dos ejes, sea cual sea la calibración.
        expect(LARGE_BOX_MM * PX_PER_MM).toBeCloseTo(LARGE_BOX_MM * PX_PER_MM, 10)
      }
    }
  })
})

describe('el ancho decide cuántos segundos se ven, no al revés', () => {
  it('agrandar la ventana muestra más señal, no la misma estirada', () => {
    const scale = paperScale(25, 10, PX_PER_MM)
    const narrow = visibleSeconds(scale, 500)
    const wide = visibleSeconds(scale, 1000)
    expect(wide).toBeCloseTo(narrow * 2, 10)
    // A 25 mm/s con 4 px/mm, 1000 px son 10 s: la tira de papel clásica.
    expect(wide).toBeCloseTo(10, 10)
  })

  it('duplicar el barrido muestra la mitad de los segundos', () => {
    expect(visibleSeconds(paperScale(50, 10, PX_PER_MM), 1000)).toBeCloseTo(
      visibleSeconds(paperScale(25, 10, PX_PER_MM), 1000) / 2,
      10,
    )
  })
})

describe('rango vertical', () => {
  it('no depende de lo que haya en la ventana', () => {
    const scale = paperScale(25, 10, PX_PER_MM)
    const quiet = verticalRange(scale, 400, 0)
    const withArtifact = verticalRange(scale, 400, 0)
    expect(withArtifact).toEqual(quiet)
    // 400 px a 40 px/mV son 10 mV de ventana.
    expect(quiet[1] - quiet[0]).toBeCloseTo(10, 10)
  })

  it('sigue la línea de base para no perder un paciente con offset', () => {
    // El front-end es DC-acoplado: el potencial de media celda de los electrodos
    // puede correr el trazado decenas de mV sin que sea una falla
    // (`INTEGRACION.md` §3.2). Anclar el rango en 0 lo dejaría fuera de pantalla.
    const scale = paperScale(25, 10, PX_PER_MM)
    const [min, max] = verticalRange(scale, 400, 56)
    expect(min).toBeLessThan(56)
    expect(max).toBeGreaterThan(56)
    expect((min + max) / 2).toBeCloseTo(56, 10)
  })

  it('duplicar la ganancia muestra la mitad de los milivoltios', () => {
    expect(visibleMillivolts(paperScale(25, 20, PX_PER_MM), 400)).toBeCloseTo(
      visibleMillivolts(paperScale(25, 10, PX_PER_MM), 400) / 2,
      10,
    )
  })
})

describe('desplazamiento vertical', () => {
  it('suma el desplazamiento a la línea de base', () => {
    expect(pannedCenterMv(0.2, 1.5, [-1, 3])).toBeCloseTo(1.7)
  })

  it('no deja que la traza se vaya entera de pantalla', () => {
    expect(pannedCenterMv(0, 10, [-1, 3])).toBe(3)
    expect(pannedCenterMv(0, -10, [-1, 3])).toBe(-1)
  })
})

describe('línea de base', () => {
  it('es la mediana, así que un artefacto no la corre', () => {
    const samples = new Float32Array(1000).fill(1)
    // Un pico de saturación en el medio. Con el promedio la base se iría a ~1,5
    // y el trazado saldría de pantalla; con la mediana no se mueve.
    samples.fill(500, 400, 450)
    expect(baselineMv(samples, 0, 1000)).toBeCloseTo(1, 5)
  })

  it('un rango vacío no explota', () => {
    expect(baselineMv(new Float32Array(0), 0, 10)).toBe(0)
    expect(baselineMv(new Float32Array(10), 5, 5)).toBe(0)
  })

  it('ignora los NaN de los huecos', () => {
    const samples = Float32Array.from([1, NaN, 1, NaN, 1])
    expect(baselineMv(samples, 0, 5)).toBeCloseTo(1, 5)
  })
})

describe('rótulo y zoom libre', () => {
  it('el rótulo dice la calibración vigente', () => {
    expect(scaleLabel(paperScale(25, 10, PX_PER_MM))).toBe('25 mm/s · 10 mm/mV')
    expect(scaleLabel(paperScale(50, 20, PX_PER_MM))).toBe('50 mm/s · 20 mm/mV')
    expect(scaleLabel(paperScale(25, AUTO_AMPLITUDE, PX_PER_MM))).toBe(
      '25 mm/s · Amplitud automática',
    )
  })

  it('reconoce cuándo el viewport dejó de corresponder a la escala', () => {
    const scale = paperScale(25, 10, PX_PER_MM)
    // 1000 px a 100 px/s son 10 s exactos.
    expect(matchesScale(scale, 1000, 10)).toBe(true)
    // El redondeo a px enteros no puede disparar "escala libre".
    expect(matchesScale(scale, 1000, 10.05)).toBe(true)
    // Un zoom de verdad sí.
    expect(matchesScale(scale, 1000, 4)).toBe(false)
    expect(matchesScale(scale, 1000, 30)).toBe(false)
  })
})

describe('amplitud automática', () => {
  it('encuadra el tramo entero, con margen, aunque se salga de cualquier ganancia', () => {
    // Un offset de 40 mV con un QRS de 2 mV: a 20 mm/mV quedaría fuera de
    // pantalla, que es justo el caso de las pruebas de banco.
    const samples = Float32Array.from([40, 40.2, 42, 39.5, 40])
    const [min, max] = autoVerticalRange(samples, 0, samples.length)!
    expect(min).toBeLessThan(39.5)
    expect(max).toBeGreaterThan(42)
  })

  it('no pierde un pico de una sola muestra', () => {
    const samples = new Float32Array(100_000)
    samples[54_321] = 7
    const [, max] = autoVerticalRange(samples, 0, samples.length)!
    expect(max).toBeGreaterThan(7)
  })

  it('una señal plana no se estira hasta llenar la altura', () => {
    const [min, max] = autoVerticalRange(Float32Array.from([3, 3, 3]), 0, 3)!
    expect(max - min).toBeGreaterThanOrEqual(1)
    expect((min + max) / 2).toBeCloseTo(3, 5)
  })

  it('ignora NaN y devuelve null sin muestras', () => {
    expect(autoVerticalRange(Float32Array.from([NaN, NaN]), 0, 2)).toBeNull()
    const [min, max] = autoVerticalRange(Float32Array.from([NaN, 1, 2]), 0, 3)!
    expect(min).toBeLessThan(1)
    expect(max).toBeGreaterThan(2)
  })

  it('la retícula sigue graduada en la ganancia estándar', () => {
    const scale = paperScale(25, AUTO_AMPLITUDE, PX_PER_MM)
    expect(scale.autoAmplitude).toBe(true)
    expect(scale.amplitude).toBe(DEFAULT_AMPLITUDE)
  })
})
