import { fileURLToPath } from 'node:url'

/** Signed-in browser state written by `auth.setup.ts` and reused by the `app` project. */
export const AUTH_FILE = fileURLToPath(new URL('../.auth/user.json', import.meta.url))

/** Gitignored (`.env.*.local`). Holds E2E_EMAIL and E2E_PASSWORD. Template: `.env.e2e.example`. */
export const ENV_FILE = fileURLToPath(new URL('../../.env.e2e.local', import.meta.url))

export function requireEnv(name: string): string {
  const value = process.env[name]?.trim()
  if (!value) {
    throw new Error(`${name} is not set. Add it to front/.env.e2e.local (see .env.e2e.example).`)
  }
  return value
}
