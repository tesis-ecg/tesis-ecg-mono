import { useCallback, useEffect, useState } from 'react'

import type { Measurement, ChartTool } from './measure'

/**
 * Estado de las herramientas de medición de un estudio.
 *
 * Las mediciones no se persisten (las notas ancladas necesitan backend): viven
 * mientras el estudio está abierto y se descartan al cambiar `resetKey` (el id
 * del estudio), para que no se arrastre una medición de un paciente a otro.
 * Esc apaga la herramienta; si el overlay estaba cancelando una medición en
 * curso ya marcó el evento con `preventDefault` y acá se respeta.
 */
export function useMeasurements(resetKey: string) {
  const [tool, setTool] = useState<ChartTool | null>(null)
  const [measurements, setMeasurements] = useState<Measurement[]>([])
  const [seenKey, setSeenKey] = useState(resetKey)

  // Reset durante el render (patrón de React para derivar estado de una prop):
  // evita un frame con las mediciones del estudio anterior.
  if (seenKey !== resetKey) {
    setSeenKey(resetKey)
    setTool(null)
    setMeasurements([])
  }

  useEffect(() => {
    if (tool == null) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (document.querySelector('[role="dialog"]')) return
      setTool(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [tool])

  const clear = useCallback(() => setMeasurements([]), [])

  return { tool, setTool, measurements, setMeasurements, clear }
}
