import { describe, expect, it } from 'vitest'

import { isVestMisplaced, VEST_ALERT_WINDOW_MS, VEST_MISPLACED_KIND } from './vestStatus'
import type { PatientAlert } from './types'

const NOW = new Date('2026-08-31T12:00:00Z').getTime()

function alert(kind: string, minutesAgo: number): PatientAlert {
  return {
    id: `${kind}-${minutesAgo}`,
    kind,
    severity: 'high',
    message: '',
    detectedAt: new Date(NOW - minutesAgo * 60_000).toISOString(),
    requiresResponse: false,
    needsReport: false,
    reportId: null,
    answeredAt: null,
  }
}

describe('isVestMisplaced', () => {
  it('marca el chaleco mal colocado cuando el aviso es reciente', () => {
    expect(isVestMisplaced([alert(VEST_MISPLACED_KIND, 40)], NOW)).toBe(true)
  })

  it('lo deja de marcar pasada la ventana', () => {
    // El backend no avisa que el episodio se cerró: lo único que dice que el
    // chaleco se acomodó es que dejaron de llegar avisos.
    const minutes = VEST_ALERT_WINDOW_MS / 60_000 + 1
    expect(isVestMisplaced([alert(VEST_MISPLACED_KIND, minutes)], NOW)).toBe(false)
  })

  it('no lo marca con avisos de otro tipo, por recientes que sean', () => {
    expect(isVestMisplaced([alert('afib', 1), alert('tachycardia', 2)], NOW)).toBe(false)
  })

  it('encuentra el aviso aunque no sea el primero de la lista', () => {
    // La lista viene ordenada por `created_at` y acá se mira `detectedAt`: en
    // los avisos cargados de golpe los dos órdenes no coinciden.
    const alerts = [alert('afib', 5), alert('pause', 10), alert(VEST_MISPLACED_KIND, 20)]
    expect(isVestMisplaced(alerts, NOW)).toBe(true)
  })

  it('ignora una fecha que no se puede leer en vez de romper la pantalla', () => {
    const broken = { ...alert(VEST_MISPLACED_KIND, 1), detectedAt: 'no-es-una-fecha' }
    expect(isVestMisplaced([broken], NOW)).toBe(false)
  })

  it('sin avisos no hay nada que marcar', () => {
    expect(isVestMisplaced([], NOW)).toBe(false)
  })
})
