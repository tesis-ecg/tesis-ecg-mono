// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HolterMetrics, Study, StudyFindings } from '@/features/studies/types'

const hooks = vi.hoisted(() => ({ findings: vi.fn(), metrics: vi.fn() }))

vi.mock('@/features/studies/hooks/useStudyFindings', () => ({
  useStudyFindings: hooks.findings,
}))
vi.mock('@/features/studies/hooks/useHolterMetrics', () => ({
  useHolterMetrics: hooks.metrics,
}))

import { StudyAnalysisTab } from './StudyAnalysisTab'

const study: Study = {
  id: 'study-1',
  patientId: 'patient-1',
  patientName: 'Ana Pérez',
  deviceId: 'device-1',
  startedAt: '2026-09-16T12:00:00Z',
  endedAt: '2026-09-17T12:00:00Z',
  durationMs: 86_400_000,
  deviceSerial: 'HOL-001',
  canAccessDevice: true,
  lastDataReceivedAt: '2026-09-17T12:00:00Z',
  status: 'completed',
}

const T0 = Date.UTC(2026, 8, 16, 12, 0, 0)

function findings(overrides: Partial<StudyFindings> = {}): StudyFindings {
  return {
    studyId: 'study-1',
    sampleRate: 500,
    sampleCount: 1_800_000,
    durationMs: 3_600_000,
    modelVersion: 'ml-1',
    quality: {
      analyzableRatio: 0.725,
      goodRatio: 0.725,
      marginalRatio: 0.075,
      badRatio: 0.2,
      evaluatedMs: 3_600_000,
      intervals: [
        {
          startOffsetMs: 0,
          endOffsetMs: 2_610_000,
          startEpochMs: T0,
          endEpochMs: T0 + 2_610_000,
          level: 'good',
          reason: 'ok',
        },
        {
          startOffsetMs: 2_610_000,
          endOffsetMs: 3_060_000,
          startEpochMs: T0 + 2_610_000,
          endEpochMs: T0 + 3_060_000,
          level: 'bad',
          reason: 'bassqi',
        },
        {
          startOffsetMs: 3_060_000,
          endOffsetMs: 3_330_000,
          startEpochMs: T0 + 3_060_000,
          endEpochMs: T0 + 3_330_000,
          level: 'marginal',
          reason: 'bsqi',
        },
        // Motivo de un estudio analizado antes de separar los índices.
        {
          startOffsetMs: 3_330_000,
          endOffsetMs: 3_600_000,
          startEpochMs: T0 + 3_330_000,
          endEpochMs: T0 + 3_600_000,
          level: 'bad',
          reason: 'spectral',
        },
      ],
    },
    groups: [
      {
        key: 'kind:tachycardia',
        kind: 'tachycardia',
        category: 'clinical',
        severity: 'high',
        occurrences: 2,
        firstOffsetMs: 600_000,
        lastOffsetMs: 1_230_000,
        firstEpochMs: T0 + 600_000,
        lastEpochMs: T0 + 1_230_000,
        items: [
          {
            id: 'finding-tachy-1',
            kind: 'tachycardia',
            category: 'clinical',
            severity: 'high',
            startOffsetMs: 600_000,
            endOffsetMs: 630_000,
            startEpochMs: T0 + 600_000,
            endEpochMs: T0 + 630_000,
            confidenceScore: 0.9,
            modelVersion: 'ml-1',
            validationStatus: 'pending',
          },
          {
            id: 'finding-tachy-2',
            kind: 'tachycardia',
            category: 'clinical',
            severity: 'high',
            startOffsetMs: 1_200_000,
            endOffsetMs: 1_230_000,
            startEpochMs: T0 + 1_200_000,
            endEpochMs: T0 + 1_230_000,
            confidenceScore: 0.8,
            modelVersion: 'ml-1',
            validationStatus: 'pending',
          },
        ],
      },
      {
        key: 'cluster:3',
        kind: 'recurrent_morphology',
        category: 'clinical',
        severity: 'medium',
        occurrences: 0,
        beatCount: 412,
        burdenPct: 3.25,
        firstOffsetMs: 60_000,
        lastOffsetMs: 3_000_000,
        firstEpochMs: T0 + 60_000,
        lastEpochMs: T0 + 3_000_000,
        items: [],
      },
    ],
    ungrouped: [],
    totals: { tachycardia: 2, recurrent_morphology: 1 },
    truncated: false,
    ...overrides,
  }
}

function metrics(overrides: Partial<HolterMetrics> = {}): HolterMetrics {
  return {
    status: 'ok',
    unavailableReason: null,
    analysis: {
      algorithmVersion: 2,
      analyzedUntilSample: 1_800_000,
      analyzedMs: 3_300_000,
      excludedMs: 300_000,
      rrIntervals: 4000,
      nnIntervals: 3900,
    },
    heartRate: {
      averageBpm: 72.4,
      min: { value: 48, sampleIndex: 100, epochMs: T0 + 120_000 },
      max: { value: 131, sampleIndex: 200, epochMs: T0 + 900_000 },
      totalBeats: 4321,
      abnormalBeats: null,
      abnormalPerThousand: null,
      windowBeats: 8,
    },
    pauses: {
      thresholdMs: 2000,
      count: 3,
      longest: { value: 2450, sampleIndex: 300, epochMs: T0 + 1_500_000, durationMs: 2450 },
      items: [],
    },
    supraventricular: null,
    ventricular: null,
    ectopyUnavailableReason: 'BEAT_CLASSIFICATION_UNAVAILABLE',
    hrvTime: {
      sdnnMs: 112.44,
      sdannMs: 98,
      rmssdMs: 35.1,
      pnn50Percent: 12.34,
      cv: 0.1,
      meanNnMs: 830,
    },
    hrvFrequency: null,
    st: [],
    hourly: [],
    rrHistogram: null,
    ...overrides,
  }
}

function query<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null, refetch: vi.fn() }
}

beforeEach(() => {
  hooks.findings.mockReturnValue(query(findings()))
  hooks.metrics.mockReturnValue(query(metrics()))
})

afterEach(cleanup)

function renderTab(onLocate = vi.fn()) {
  render(<StudyAnalysisTab study={study} onLocate={onLocate} />)
  return onLocate
}

describe('StudyAnalysisTab', () => {
  it('muestra un estado de carga mientras llegan el análisis y las métricas', () => {
    hooks.findings.mockReturnValue({ ...query(undefined), isLoading: true })
    hooks.metrics.mockReturnValue({ ...query(undefined), isLoading: true })

    renderTab()

    const statuses = screen.getAllByRole('status').map((node) => node.textContent)
    expect(statuses).toEqual(
      expect.arrayContaining([
        expect.stringContaining('Cargando calidad de la señal…'),
        expect.stringContaining('Cargando métricas de ritmo…'),
        expect.stringContaining('Cargando hallazgos…'),
      ]),
    )
  })

  it('ofrece reintentar cuando falla el análisis, con un solo aviso para calidad y hallazgos', () => {
    const refetch = vi.fn()
    hooks.findings.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { status: 500, code: 'SERVER_ERROR', message: 'Error del servidor' },
      refetch,
    })

    renderTab()

    expect(screen.getByText('No pudimos cargar la calidad ni los hallazgos')).toBeTruthy()
    const retries = screen.getAllByRole('button', { name: 'Reintentar' })
    expect(retries).toHaveLength(1)
    fireEvent.click(retries[0])
    expect(refetch).toHaveBeenCalledOnce()
    // Las métricas son otra consulta: siguen visibles.
    expect(screen.getByText('72 lpm')).toBeTruthy()
  })

  it('mantiene los datos a la vista si falla un refresco del sondeo', () => {
    hooks.findings.mockReturnValue({ ...query(findings()), isError: true, error: new Error('red') })

    renderTab()

    expect(screen.queryByText('No pudimos cargar la calidad ni los hallazgos')).toBeNull()
    expect(screen.getByText('72,5 %')).toBeTruthy()
  })

  it('distingue la falta de hallazgos de la señal todavía no evaluada', () => {
    hooks.findings.mockReturnValue(
      query(
        findings({
          groups: [],
          ungrouped: [],
          quality: {
            analyzableRatio: 0,
            goodRatio: 0,
            marginalRatio: 0,
            badRatio: 0,
            evaluatedMs: 0,
            intervals: [],
          },
        }),
      ),
    )
    hooks.metrics.mockReturnValue(
      query(metrics({ status: 'pending', unavailableReason: 'ANALYSIS_PENDING', analysis: null })),
    )

    renderTab()

    expect(screen.getByText('Calidad todavía no evaluada')).toBeTruthy()
    expect(screen.getByText('Sin hallazgos')).toBeTruthy()
    // Sin señal evaluada, "sin hallazgos" no significa "registro limpio": las dos
    // tarjetas lo dicen.
    expect(
      screen.getAllByText('El motor de detección todavía no analizó señal de este estudio.'),
    ).toHaveLength(2)
    expect(screen.getByText('Métricas de ritmo no disponibles')).toBeTruthy()
    expect(screen.getByText('Análisis de latidos pendiente.')).toBeTruthy()
  })

  it('muestra calidad, ritmo y hallazgos con los formatos del informe', () => {
    renderTab()

    // Calidad: proporciones y motivos legibles, incluido el `spectral` legacy.
    expect(screen.getByText('72,5 %')).toBeTruthy()
    expect(screen.getByText('7,5 %')).toBeTruthy()
    expect(screen.getByText('20,0 %')).toBeTruthy()
    expect(screen.getByText('Deriva de la línea de base (basSQI)')).toBeTruthy()
    expect(screen.getByText('Índices espectrales fuera de rango')).toBeTruthy()
    expect(screen.getByText('Detectores de latidos en desacuerdo (bSQI)')).toBeTruthy()
    expect(screen.queryByText('spectral')).toBeNull()

    // Ritmo: las métricas de `/holter-metrics`, con los formateadores del PDF.
    expect(screen.getByText('72 lpm')).toBeTruthy()
    expect(screen.getByText('48 lpm')).toBeTruthy()
    expect(screen.getByText('131 lpm')).toBeTruthy()
    expect(screen.getByText('Pausas R-R > 2000 ms')).toBeTruthy()
    expect(screen.getByText('2,45 s')).toBeTruthy()
    expect(screen.getByText('112,4 ms')).toBeTruthy()
    expect(screen.getByText('35,1 ms')).toBeTruthy()
    expect(screen.getByText('12,3 %')).toBeTruthy()
    expect(screen.getByText('0 h 55 min 0 s')).toBeTruthy()
    expect(screen.getByText('0 h 5 min 0 s')).toBeTruthy()

    // Hallazgos: rótulos del catálogo de anotaciones, severidad y carga.
    expect(screen.getByText('Soporte a la decisión — no diagnóstico')).toBeTruthy()
    const tachycardia = screen.getByText('Taquicardia').closest('li')!
    expect(within(tachycardia).getByText('Alta')).toBeTruthy()
    expect(within(tachycardia).getByText('2 episodios')).toBeTruthy()
    const morphology = screen.getByText('Morfología recurrente').closest('li')!
    expect(within(morphology).getByText('Media')).toBeTruthy()
    expect(
      within(morphology).getByText('412 latidos · carga 3,3 % · morfología n.º 3'),
    ).toBeTruthy()

    // Ni intervalos ni QT: el módulo sigue siendo experimental.
    expect(screen.queryByText(/QT/)).toBeNull()
    expect(screen.queryByText(/Intervalos/)).toBeNull()
  })

  it('lleva al visor al episodio que se toca, con su hora de pared', () => {
    const onLocate = renderTab()

    const tachycardia = screen.getByText('Taquicardia').closest('li')!
    const episodes = within(tachycardia).getAllByRole('button', { name: /^Ver Taquicardia del/ })
    fireEvent.click(episodes[1])

    expect(onLocate).toHaveBeenCalledOnce()
    expect(onLocate.mock.calls[0][0]).toMatchObject({
      id: 'finding-tachy-2',
      kind: 'tachycardia',
      startMs: T0 + 1_200_000,
      endMs: T0 + 1_230_000,
    })
  })

  it('el encabezado de un grupo sin episodios lleva al primer latido de la morfología', () => {
    const onLocate = renderTab()

    fireEvent.click(screen.getByRole('button', { name: /Morfología recurrente/ }))

    expect(onLocate.mock.calls[0][0]).toMatchObject({
      startMs: T0 + 60_000,
      endMs: T0 + 60_000,
    })
  })

  it('el encabezado de un grupo lleva a su primer episodio', () => {
    const onLocate = renderTab()

    fireEvent.click(screen.getByText('Taquicardia').closest('button')!)

    expect(onLocate.mock.calls[0][0]).toMatchObject({
      id: 'finding-tachy-1',
      startMs: T0 + 600_000,
      endMs: T0 + 630_000,
    })
  })

  it('en un grupo recortado el encabezado lleva a la hora que muestra, no al primero listado', () => {
    // El backend lista los episodios más atípicos (por score), no los primeros:
    // el encabezado dice "desde las 12:01" y el primero listado es de las 12:10.
    const [tachycardia, cluster] = findings().groups ?? []
    hooks.findings.mockReturnValue(
      query(
        findings({
          groups: [
            {
              ...tachycardia!,
              occurrences: 14,
              firstOffsetMs: 60_000,
              firstEpochMs: T0 + 60_000,
            },
            cluster!,
          ],
          truncated: true,
        }),
      ),
    )
    const onLocate = renderTab()

    fireEvent.click(screen.getByText('Taquicardia').closest('button')!)

    expect(onLocate.mock.calls[0][0]).toMatchObject({
      kind: 'tachycardia',
      startMs: T0 + 60_000,
      endMs: T0 + 60_000,
    })
  })

  it('lleva al visor a la evidencia de la FC mínima y de la pausa más larga', () => {
    const onLocate = renderTab()

    fireEvent.click(screen.getByRole('button', { name: 'Ver FC mínima en el ECG' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ver Pausa más larga en el ECG' }))

    expect(onLocate.mock.calls[0][0]).toMatchObject({
      kind: 'hr_min',
      startMs: T0 + 120_000,
      endMs: T0 + 120_000,
    })
    expect(onLocate.mock.calls[1][0]).toMatchObject({
      kind: 'pause_longest',
      startMs: T0 + 1_500_000,
      endMs: T0 + 1_500_000 + 2450,
    })
  })

  it('refresca mientras el estudio está en curso', () => {
    render(<StudyAnalysisTab study={{ ...study, status: 'in_progress' }} onLocate={vi.fn()} />)

    expect(hooks.findings).toHaveBeenLastCalledWith('study-1', true)
    expect(hooks.metrics).toHaveBeenLastCalledWith('study-1', true)
  })
})
