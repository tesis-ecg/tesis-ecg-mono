import { createContext, useContext, type Dispatch, type SetStateAction } from 'react'

import type { PaperScaleState } from './usePaperScale'
import type { ECGAnnotationGroup, ECGTimelineRange, ECGViewportChange } from '../types'

export interface EcgSession {
  viewport: ECGViewportChange | null
  setViewport: Dispatch<SetStateAction<ECGViewportChange | null>>
  timelineWindow: ECGTimelineRange | null
  setTimelineWindow: Dispatch<SetStateAction<ECGTimelineRange | null>>
  selectedGroup: ECGAnnotationGroup | null
  setSelectedGroup: Dispatch<SetStateAction<ECGAnnotationGroup | null>>
  cursorMs: number | null
  setCursorMs: Dispatch<SetStateAction<number | null>>
  selectedAnnotationId: string | null
  setSelectedAnnotationId: Dispatch<SetStateAction<string | null>>
  showAnnotationsOnChart: boolean
  setShowAnnotationsOnChart: Dispatch<SetStateAction<boolean>>
  scale: PaperScaleState
}

export const EcgSessionContext = createContext<EcgSession | null>(null)

export function useEcgSession() {
  const session = useContext(EcgSessionContext)
  if (!session) throw new Error('ECG viewer requires an EcgSessionRoute')
  return session
}
