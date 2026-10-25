import { useState, type ReactNode } from 'react'
import { Outlet, useParams, useSearchParams } from 'react-router-dom'

import { EcgSessionContext } from '../hooks/useEcgSession'
import { usePaperScale } from '../hooks/usePaperScale'
import type { AmplitudeMode } from '../paperScale'
import type { ECGAnnotationGroup, ECGTimelineRange, ECGViewportChange } from '../types'

/** Estado efímero compartido por las dos rutas; nunca se hereda entre estudios. */
export function EcgSessionRoute({ amplitude = 20 }: { amplitude?: AmplitudeMode }) {
  const { id } = useParams()
  const [search] = useSearchParams()
  return (
    <EcgSessionProvider key={id ?? search.get('seed') ?? 'playground'} amplitude={amplitude}>
      <Outlet />
    </EcgSessionProvider>
  )
}

export function EcgSessionProvider({
  children,
  amplitude = 20,
}: {
  children: ReactNode
  amplitude?: AmplitudeMode
}) {
  const [viewport, setViewport] = useState<ECGViewportChange | null>(null)
  const [timelineWindow, setTimelineWindow] = useState<ECGTimelineRange | null>(null)
  const [selectedGroup, setSelectedGroup] = useState<ECGAnnotationGroup | null>(null)
  const [cursorMs, setCursorMs] = useState<number | null>(null)
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(null)
  const [showAnnotationsOnChart, setShowAnnotationsOnChart] = useState(true)
  const scale = usePaperScale({ paperSpeed: 25, amplitude })

  return (
    <EcgSessionContext.Provider
      value={{
        viewport,
        setViewport,
        timelineWindow,
        setTimelineWindow,
        selectedGroup,
        setSelectedGroup,
        cursorMs,
        setCursorMs,
        selectedAnnotationId,
        setSelectedAnnotationId,
        showAnnotationsOnChart,
        setShowAnnotationsOnChart,
        scale,
      }}
    >
      {children}
    </EcgSessionContext.Provider>
  )
}
