import { describe, expect, it } from 'vitest'

import { humanize, labelFor } from './labels'

describe('labelFor', () => {
  it('usa la etiqueta del catálogo cuando llegó', () => {
    const labels = new Map([['dolor_pecho', 'Dolor en el pecho']])
    expect(labelFor(labels, 'dolor_pecho')).toBe('Dolor en el pecho')
  })

  it('no muestra el slug crudo mientras el catálogo todavía no cargó', () => {
    // El historial se dibuja antes de que llegue `GET /mobile/catalogs`, y con
    // el fallback plano de antes el paciente leía `dolor_pecho` en pantalla.
    expect(labelFor(new Map(), 'dolor_pecho')).toBe('Dolor pecho')
    expect(labelFor(new Map(), 'falta_aire')).toBe('Falta aire')
  })

  it('degrada igual con un slug retirado del catálogo', () => {
    // Los registros viejos guardan slugs que pueden ya no existir; tienen que
    // seguir siendo legibles y no volverse un hueco en la pantalla.
    const labels = new Map([['palpitaciones', 'Palpitaciones']])
    expect(labelFor(labels, 'sintoma_viejo')).toBe('Sintoma viejo')
  })
})

describe('humanize', () => {
  it('no rompe con un slug vacío', () => {
    expect(humanize('')).toBe('')
    expect(humanize('_')).toBe('_')
  })
})
