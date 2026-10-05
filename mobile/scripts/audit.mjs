// `npm audit --audit-level=high` fails on any high advisory, including ones that no
// release fixes yet, so a single unfixable advisory inside the Expo toolchain keeps the
// `mobile` CI job red for everyone. This runs the same check but lets through the
// advisories listed in ALLOWED, each with the reason it is safe to carry here.
//
// An entry is a decision, not a silencer: keep the list short and delete an entry as soon
// as its package ships a fix (the script warns when an entry stops showing up).
import { spawnSync } from 'node:child_process'

const ALLOWED = {
  // braces <= 3.0.3 (3.0.3 is the latest release), reached through micromatch, which Metro
  // and the Expo CLI use to expand glob patterns taken from this project's own config.
  // Stack exhaustion needs an attacker-chosen, deeply nested brace pattern, and none comes
  // from outside. It is build tooling, not part of the app bundle.
  'GHSA-vfj7-8cjw-p6xm': 'braces: no patched version, only reached through Metro and the Expo CLI',
  // node-forge <= 1.4.0 (1.4.0 is the latest release), reached only through @expo/cli's
  // code-signing helpers (EAS Update). This app does not use expo-updates, and the flaw is in
  // verifying RSA PKCS#1 v1.5 signatures made by someone else.
  'GHSA-86w9-cpqp-85rv': 'node-forge: no patched version, only reached through @expo/cli',
}

const BLOCKING = new Set(['high', 'critical'])

function fail(message) {
  console.error(message)
  process.exit(1)
}

const audit = spawnSync('npm', ['audit', '--json'], {
  encoding: 'utf8',
  maxBuffer: 64 * 1024 * 1024,
})

// npm exits non-zero whenever it finds something, so the exit code says nothing: read the report.
let report
try {
  report = JSON.parse(audit.stdout)
} catch {
  fail(`npm audit did not return JSON:\n${audit.stdout}${audit.stderr}`)
}
// Offline or registry trouble is a failed audit, never a clean one.
if (report.error) fail(`npm audit failed: ${report.message || report.error.summary || JSON.stringify(report.error)}`)

// Each advisory sits in `via` of the package it affects; a string there only names another
// vulnerable package that this one depends on.
const advisories = new Map()
for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vulnerability.via) {
    if (typeof via === 'string' || !BLOCKING.has(via.severity)) continue
    advisories.set(via.url?.split('/').pop() ?? String(via.source), via)
  }
}

const blocking = [...advisories].filter(([id]) => !(id in ALLOWED))

for (const [id, advisory] of advisories) {
  if (id in ALLOWED) console.log(`allowed  ${id}  ${ALLOWED[id]}`)
  else console.log(`BLOCKING ${id}  ${advisory.name} (${advisory.severity}): ${advisory.title}`)
}
for (const id of Object.keys(ALLOWED)) {
  if (!advisories.has(id)) {
    console.log(`::warning::${id} is no longer reported by npm audit: remove it from ALLOWED in mobile/scripts/audit.mjs`)
  }
}

if (blocking.length > 0) {
  fail(`\n${blocking.length} high or critical advisories with no exception. Run \`npm audit\` for the dependency paths.`)
}
console.log(`\nnpm audit: no high or critical advisories beyond the ${advisories.size} allowed above.`)
