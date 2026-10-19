import { describe, expect, it } from 'vitest'

import { NO_SYMPTOMS, OTHER, toggleSymptom, validateReport } from './reportSchema'

const complete = {
  symptoms: ['palpitaciones'],
  symptomsOther: '',
  activity: 'caminando',
  activityOther: '',
  notes: '',
}

describe('validateReport', () => {
  it('acepta un registro completo', () => {
    expect(validateReport(complete)).toBeNull()
  })

  it('exige elegir al menos un síntoma', () => {
    expect(validateReport({ ...complete, symptoms: [] })).toMatch(/al menos una opción/i)
  })

  it('exige elegir una actividad', () => {
    expect(validateReport({ ...complete, activity: '' })).toMatch(/qué estabas haciendo/i)
  })

  it('rechaza "no sentí nada" combinado con un síntoma', () => {
    // El médico no puede interpretar un registro que dice las dos cosas.
    const invalid = { ...complete, symptoms: [NO_SYMPTOMS, 'dolor_pecho'] }
    expect(validateReport(invalid)).toMatch(/no se combina/i)
  })

  it('acepta "no sentí nada" solo', () => {
    expect(validateReport({ ...complete, symptoms: [NO_SYMPTOMS] })).toBeNull()
  })

  it('exige el texto libre cuando el síntoma es "otro"', () => {
    const invalid = { ...complete, symptoms: [OTHER], symptomsOther: '   ' }
    expect(validateReport(invalid)).toMatch(/qué sentiste/i)
    expect(validateReport({ ...invalid, symptomsOther: 'un pinchazo' })).toBeNull()
  })

  it('exige el texto libre cuando la actividad es "otro"', () => {
    const invalid = { ...complete, activity: OTHER, activityOther: '' }
    expect(validateReport(invalid)).toMatch(/qué estabas haciendo/i)
    expect(validateReport({ ...invalid, activityOther: 'cortando el pasto' })).toBeNull()
  })
})

describe('toggleSymptom', () => {
  it('agrega y saca un síntoma', () => {
    expect(toggleSymptom([], 'mareo')).toEqual(['mareo'])
    expect(toggleSymptom(['mareo'], 'mareo')).toEqual([])
  })

  it('elegir "no sentí nada" descarta el resto', () => {
    expect(toggleSymptom(['mareo', 'dolor_pecho'], NO_SYMPTOMS)).toEqual([NO_SYMPTOMS])
  })

  it('elegir un síntoma descarta "no sentí nada"', () => {
    // La otra dirección de la misma regla. Es la que se olvida y deja pasar un
    // registro contradictorio hasta que lo rechaza el backend.
    expect(toggleSymptom([NO_SYMPTOMS], 'mareo')).toEqual(['mareo'])
  })

  it('destildar "no sentí nada" deja la lista vacía', () => {
    expect(toggleSymptom([NO_SYMPTOMS], NO_SYMPTOMS)).toEqual([])
  })
})
