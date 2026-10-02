// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ holter: vi.fn(), health: vi.fn() }))

vi.mock('@/features/devices/hooks/useHolter', () => ({ useHolter: hooks.holter }))
vi.mock('@/features/devices/hooks/useHolterHealth', () => ({ useHolterHealth: hooks.health }))

import { StudyDeviceTab } from './StudyDeviceTab'

beforeEach(() => {
  hooks.holter.mockReturnValue({
    data: {
      id: 'device-1',
      serial: 'HOL-001',
      model: 'Holter ECG',
      firmwareVersion: '1.2.3',
      status: 'assigned',
      assignedPatientId: 'patient-1',
      assignedPatientName: 'Ana Pérez',
      activeStudyId: 'study-1',
      lastSeenAt: '2026-09-16T12:00:00Z',
      createdAt: '2026-01-10T12:00:00Z',
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  hooks.health.mockReturnValue({
    data: {
      deviceId: 'device-1',
      serial: 'HOL-001',
      model: 'Holter ECG',
      firmwareVersion: '1.2.3',
      telemetryAvailable: true,
      batteryPercent: 82,
      signalDbm: -55,
      signalQuality: 'good',
      lastPingAt: '2026-09-16T12:00:00Z',
      nextScheduledUploadAt: null,
      uploadsToday: 4,
      storageUsedMb: 128,
      storageTotalMb: 512,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
})

afterEach(cleanup)

describe('StudyDeviceTab', () => {
  it('muestra el resumen y la telemetría del dispositivo en modo lectura', () => {
    render(<StudyDeviceTab deviceId="device-1" />)

    expect(screen.getAllByText('HOL-001').length).toBeGreaterThan(0)
    expect(screen.getByText('v1.2.3')).toBeTruthy()
    expect(screen.getByText('82%')).toBeTruthy()
    expect(screen.queryByText('Editar Holter')).toBeNull()
    expect(screen.queryByText('API key')).toBeNull()
  })

  it('mantiene el resumen cuando el dispositivo todavía no tiene telemetría', () => {
    hooks.health.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: {
        status: 404,
        code: 'NOT_FOUND',
        serverCode: 'DEVICE_HEALTH_NOT_FOUND',
        message: 'Sin telemetría',
      },
      refetch: vi.fn(),
    })

    render(<StudyDeviceTab deviceId="device-1" />)

    expect(screen.getByText('HOL-001')).toBeTruthy()
    expect(screen.getByText('Sin telemetría disponible')).toBeTruthy()
  })

  it('muestra un estado de carga antes de exponer datos parciales', () => {
    hooks.holter.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    render(<StudyDeviceTab deviceId="device-1" />)

    expect(screen.getByRole('status').textContent).toContain('Cargando dispositivo…')
  })
})
