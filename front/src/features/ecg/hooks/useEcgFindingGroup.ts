import { useEffect, useMemo } from 'react'

import { reconcileAnnotationGroup } from '../timeline'
import type { ECGAnnotation, ECGAnnotationGroup } from '../types'

export function useEcgFindingGroup(
  annotations: ECGAnnotation[],
  selectedGroup: ECGAnnotationGroup | null,
  onGroupChange: (group: ECGAnnotationGroup | null) => void,
) {
  const group = useMemo(
    () => reconcileAnnotationGroup(selectedGroup, annotations),
    [selectedGroup, annotations],
  )
  useEffect(() => {
    if (group !== selectedGroup) onGroupChange(group)
  }, [group, selectedGroup, onGroupChange])
  return group
}
