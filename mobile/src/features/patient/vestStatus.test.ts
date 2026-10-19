import { describe, expect, it } from 'vitest'

import { isVestMisplaced } from './vestStatus'
import type { DeviceStatus, VestPlacement } from './types'

function device(overrides: Partial<DeviceStatus> = {}): DeviceStatus {
  return {
    hasDevice: true,
    state: 'recording',
    vestPlacement: 'ok',
    vestPlacementAt: '2026-08-31T12:00:00Z',
    deviceId: 'dev-1',
    serial: 'HOL-1',
    model: 'Holter ECG',
    firmwareVersion: '1.4.2',
    batteryPercent: 80,
    lastSeenAt: '2026-08-31T12:00:00Z',
    lastDataReceivedAt: '2026-08-31T12:00:00Z',
    studyId: 'study-1',
    studyStartedAt: '2026-08-31T10:00:00Z',
    ...overrides,
  }
}

describe('isVestMisplaced', () => {
  it('marca el chaleco mal colocado cuando el equipo lo reportó así', () => {
    expect(isVestMisplaced(device({ vestPlacement: 'bad' }))).toBe(true)
  })

  it('lo deja de marcar apenas el equipo reporta que se acomodó', () => {
    // Este es el caso que la heurística vieja no podía cubrir: el paciente se
    // acomoda el chaleco y el cartel tenía que seguir puesto hasta que venciera
    // una ventana de una hora.
    expect(isVestMisplaced(device({ vestPlacement: 'ok' }))).toBe(false)
  })

  it('no afirma nada con un equipo que todavía no reportó', () => {
    expect(isVestMisplaced(device({ vestPlacement: 'unknown' }))).toBe(false)
  })

  it('no marca nada sin chaleco asignado, aunque venga un estado viejo', () => {
    const placement: VestPlacement = 'bad'
    expect(isVestMisplaced(device({ hasDevice: false, vestPlacement: placement }))).toBe(false)
  })

  it('sin dato cargado todavía no pinta el cartel', () => {
    expect(isVestMisplaced(undefined)).toBe(false)
  })
})
