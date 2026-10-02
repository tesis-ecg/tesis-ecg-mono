import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { queryKeys } from '@/lib/queryKeys'

import {
  finalizeStudyClinicalReport,
  getStudyClinicalReportDraft,
  getStudyClinicalReportPreview,
  listStudyClinicalReports,
  updateStudyClinicalReportDraft,
} from '../api/studiesApi'
import type { StudyClinicalReportDraftUpdate } from '../types'

export function useStudyClinicalReportDraft(studyId: string | undefined) {
  return useQuery({
    queryKey: [...queryKeys.clinicalReports, studyId, 'draft'],
    queryFn: () => getStudyClinicalReportDraft(studyId!),
    enabled: Boolean(studyId),
  })
}

export function useStudyClinicalReportPreview(studyId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.clinicalReports, studyId, 'preview'],
    queryFn: () => getStudyClinicalReportPreview(studyId!),
    enabled: Boolean(studyId) && enabled,
  })
}

export function useStudyClinicalReportVersions(studyId: string | undefined) {
  return useQuery({
    queryKey: [...queryKeys.clinicalReports, studyId, 'versions'],
    queryFn: () => listStudyClinicalReports(studyId!),
    enabled: Boolean(studyId),
  })
}

export function useUpdateStudyClinicalReportDraft(studyId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (update: StudyClinicalReportDraftUpdate) =>
      updateStudyClinicalReportDraft(studyId, update),
    onSuccess: (draft) => {
      queryClient.setQueryData([...queryKeys.clinicalReports, studyId, 'draft'], draft)
      void queryClient.invalidateQueries({
        queryKey: [...queryKeys.clinicalReports, studyId, 'preview'],
      })
    },
  })
}

export function useFinalizeStudyClinicalReport(studyId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      pdf,
      draftRevision,
      snapshotHash,
    }: {
      pdf: ArrayBuffer
      draftRevision: number
      snapshotHash: string
    }) => finalizeStudyClinicalReport(studyId, pdf, draftRevision, snapshotHash),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...queryKeys.clinicalReports, studyId] })
    },
  })
}
