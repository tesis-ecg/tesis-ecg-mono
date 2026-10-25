// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router-dom'

import { useEcgSession } from '../hooks/useEcgSession'
import { EcgSessionRoute } from './EcgSessionRoute'

afterEach(cleanup)

const timelineWindow = { startMs: 0, endMs: 43_200_000 }
const selectedGroup = {
  kind: 'lead_off',
  startMs: 0,
  endMs: 1_000,
  severity: 'medium' as const,
  annotationIds: ['finding-1', 'finding-2'],
}

const viewport = { startMs: 1_000, endMs: 4_000, millisecondsPerPixel: 5, isClinicalScale: false }

function Probe({ expanded = false }: { expanded?: boolean }) {
  const session = useEcgSession()
  const navigate = useNavigate()
  const { id } = useParams()
  return (
    <>
      <output data-testid="session">
        {JSON.stringify({
          viewport: session.viewport,
          timelineWindow: session.timelineWindow,
          selectedGroup: session.selectedGroup,
          cursor: session.cursorMs,
          speed: session.scale.paperSpeed,
          amplitude: session.scale.amplitude,
          selection: session.selectedAnnotationId,
          annotations: session.showAnnotationsOnChart,
        })}
      </output>
      <button
        onClick={() => {
          session.setViewport(viewport)
          session.setTimelineWindow(timelineWindow)
          session.setSelectedGroup(selectedGroup)
          session.setCursorMs(2_000)
          session.scale.setPaperSpeed(50)
          session.scale.setAmplitude(10)
          session.setSelectedAnnotationId('finding-1')
          session.setShowAnnotationsOnChart(false)
        }}
      >
        Editar
      </button>
      <button onClick={() => navigate(`/studies/${id}/ecg`)}>Ampliar</button>
      <button onClick={() => navigate(-1)}>Atrás</button>
      <button onClick={() => navigate('/studies/other/ecg')}>Otro estudio</button>
      <span>{expanded ? 'ampliado' : 'detalle'}</span>
    </>
  )
}

function mount(path = '/studies/one') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="studies/:id" element={<EcgSessionRoute />}>
          <Route index element={<Probe />} />
          <Route path="ecg" element={<Probe expanded />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function state() {
  return JSON.parse(screen.getByTestId('session').textContent!)
}

describe('ECG session across study routes', () => {
  it('conserva rango, cursor, calibración y anotaciones al ampliar y volver con Atrás', () => {
    mount()
    fireEvent.click(screen.getByText('Editar'))
    const before = state()
    fireEvent.click(screen.getByText('Ampliar'))
    expect(screen.getByText('ampliado')).toBeTruthy()
    expect(state()).toEqual(before)
    expect(before).toEqual({
      viewport,
      timelineWindow,
      selectedGroup,
      cursor: 2_000,
      speed: 50,
      amplitude: 10,
      selection: 'finding-1',
      annotations: false,
    })
    fireEvent.click(screen.getByText('Atrás'))
    expect(screen.getByText('detalle')).toBeTruthy()
    expect(state()).toEqual(before)
  })

  it('acepta entrada directa y reinicia el estado cuando cambia el estudio', () => {
    mount('/studies/one/ecg')
    const initial = state()
    expect(initial).toEqual({
      viewport: null,
      timelineWindow: null,
      selectedGroup: null,
      cursor: null,
      speed: 25,
      amplitude: 20,
      selection: null,
      annotations: true,
    })
    fireEvent.click(screen.getByText('Editar'))
    fireEvent.click(screen.getByText('Otro estudio'))
    expect(state()).toEqual(initial)
  })
})
