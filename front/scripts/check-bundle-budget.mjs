import { readdirSync, readFileSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

const MAX_RAW_BYTES = 500 * 1024
const MAX_GZIP_BYTES = 180 * 1024
const WORKER_BUDGETS = [
  {
    prefix: 'clinicalReport.worker-',
    maxRawBytes: 850 * 1024,
    maxGzipBytes: 260 * 1024,
  },
]
const assetsDirectory = new URL('../dist/assets/', import.meta.url)
const failures = []

for (const filename of readdirSync(assetsDirectory)) {
  if (!filename.endsWith('.js')) continue
  const contents = readFileSync(new URL(filename, assetsDirectory))
  const gzipBytes = gzipSync(contents).byteLength
  // El informe clínico se crea en un Worker sólo al presionar “Generar PDF”.
  // jsPDF incluye el escritor PDF completo, por lo que tiene un presupuesto
  // propio: el límite general continúa protegiendo todo el código interactivo.
  const workerBudget = WORKER_BUDGETS.find((budget) => filename.startsWith(budget.prefix))
  const maxRawBytes = workerBudget?.maxRawBytes ?? MAX_RAW_BYTES
  const maxGzipBytes = workerBudget?.maxGzipBytes ?? MAX_GZIP_BYTES
  if (contents.byteLength > maxRawBytes || gzipBytes > maxGzipBytes) {
    failures.push(
      `${filename}: ${(contents.byteLength / 1024).toFixed(1)} KiB raw ` +
        `(máximo ${(maxRawBytes / 1024).toFixed(0)} KiB), ` +
        `${(gzipBytes / 1024).toFixed(1)} KiB gzip ` +
        `(máximo ${(maxGzipBytes / 1024).toFixed(0)} KiB)`,
    )
  }
}

if (failures.length > 0) {
  console.error(`Bundle fuera de presupuesto:\n${failures.join('\n')}`)
  process.exit(1)
}

console.log('Bundle dentro del presupuesto.')
