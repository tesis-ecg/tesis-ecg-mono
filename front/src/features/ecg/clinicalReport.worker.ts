/// <reference lib="webworker" />

import { buildClinicalReport } from './clinicalReport'
import type { ClinicalReportInput } from './clinicalReportTypes'

self.onmessage = (event: MessageEvent<ClinicalReportInput>) => {
  try {
    const pdf = buildClinicalReport(event.data)
    self.postMessage({ ok: true, pdf }, [pdf])
  } catch (error) {
    self.postMessage({
      ok: false,
      message: error instanceof Error ? error.message : 'No se pudo generar el informe.',
    })
  }
}

export {}
