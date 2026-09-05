import { describe, expect, it } from 'vitest'

import { routeForNotification } from './routeForNotification'

describe('routeForNotification', () => {
  it('un pedido de registro abre el formulario anclado al momento del aviso', () => {
    expect(
      routeForNotification({
        type: 'report_request',
        alertId: 'alert-1',
        occurredAt: '2026-08-30T14:30:00Z',
        kind: 'afib',
      }),
    ).toEqual({
      pathname: '/report',
      // El `kind` viaja para que el formulario pueda encabezar con qué se
      // detectó: sin eso el paciente tiene que adivinar de qué le hablamos.
      params: { alertId: 'alert-1', occurredAt: '2026-08-30T14:30:00Z', kind: 'afib' },
    })
  })

  it('un aviso viejo sin tipo igual abre el formulario', () => {
    // Los pushes que ya estaban en la bandeja antes de este cambio no traen
    // `kind`. La clave no puede viajar vacía: el formulario decide con su
    // presencia si tiene algo que nombrar.
    expect(
      routeForNotification({
        type: 'report_request',
        alertId: 'alert-1',
        occurredAt: '2026-08-30T14:30:00Z',
      }),
    ).toEqual({
      pathname: '/report',
      params: { alertId: 'alert-1', occurredAt: '2026-08-30T14:30:00Z' },
    })
  })

  it('el aviso de mala colocación lleva al estado del chaleco, no al formulario', () => {
    // No hay nada que completar: lo que tiene que hacer el paciente es
    // acomodarse el chaleco.
    expect(routeForNotification({ type: 'vest_misplaced', alertId: 'alert-2' })).toEqual({
      pathname: '/(tabs)/device',
    })
  })

  it('manda payloads incompletos o futuros al centro de avisos', () => {
    const fallback = { pathname: '/notifications' }
    expect(routeForNotification(null)).toEqual(fallback)
    expect(routeForNotification(undefined)).toEqual(fallback)
    expect(routeForNotification({ type: 'report_request' })).toEqual(fallback)
    expect(routeForNotification({ type: 'report_request', alertId: 'alert-1' })).toEqual(fallback)
    expect(
      routeForNotification({ type: 'report_request', occurredAt: '2026-08-30T14:30:00Z' }),
    ).toEqual(fallback)
    expect(routeForNotification({ type: 'algo_nuevo', alertId: 'alert-3' })).toEqual(fallback)
  })
})
