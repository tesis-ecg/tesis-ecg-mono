import { readdirSync, readFileSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

const MAX_RAW_BYTES = 500 * 1024
const MAX_GZIP_BYTES = 180 * 1024
const assetsDirectory = new URL('../dist/assets/', import.meta.url)
const failures = []

for (const filename of readdirSync(assetsDirectory)) {
  if (!filename.endsWith('.js')) continue
  const contents = readFileSync(new URL(filename, assetsDirectory))
  const gzipBytes = gzipSync(contents).byteLength
  if (contents.byteLength > MAX_RAW_BYTES || gzipBytes > MAX_GZIP_BYTES) {
    failures.push(
      `${filename}: ${(contents.byteLength / 1024).toFixed(1)} KiB raw, ` +
        `${(gzipBytes / 1024).toFixed(1)} KiB gzip`,
    )
  }
}

if (failures.length > 0) {
  console.error(`Bundle fuera de presupuesto:\n${failures.join('\n')}`)
  process.exit(1)
}

console.log('Bundle dentro del presupuesto (500 KiB raw / 180 KiB gzip por chunk).')
