import { existsSync } from 'node:fs'
import { defineConfig, devices } from '@playwright/test'
import { AUTH_FILE, ENV_FILE } from './e2e/support/session'

// Real env wins: loadEnvFile never overrides variables that are already set.
if (existsSync(ENV_FILE)) process.loadEnvFile(ENV_FILE)

// Defaults match `docker compose` and `run-holter`. Override them to run the suite on
// ports that nothing else is using: with `reuseExistingServer`, a dev stack that is
// already listening on 5173/8000 (another worktree, say) would be used instead.
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5173)
const API_PORT = Number(process.env.E2E_API_PORT ?? 8000)
const WEB_URL = `http://localhost:${WEB_PORT}`
const API_URL = `http://localhost:${API_PORT}`

export default defineConfig({
  testDir: './e2e',
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // CI: `list` for the job log, `github` for annotations on the check, and JSON
  // for .github/scripts/playwright-pr-comment.mjs, which writes failures into a
  // PR comment.
  reporter: process.env.CI
    ? [['list'], ['github'], ['json', { outputFile: 'playwright-report/results.json' }]]
    : 'list',
  use: { baseURL: WEB_URL, trace: 'on-first-retry' },
  projects: [
    // Logged-out pages: no credentials, no session.
    {
      name: 'public',
      testMatch: /public\/.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    // Signs in once through the real login form (backend -> Auth0) and saves the
    // browser state. No trace: it would record the sign-in request, password included.
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
      use: { ...devices['Desktop Chrome'], trace: 'off' },
    },
    // Signed-in flows reuse that state.
    {
      name: 'app',
      testMatch: /app\/.*\.spec\.ts/,
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], storageState: AUTH_FILE },
    },
  ],
  webServer: [
    {
      // --strictPort: if Vite silently moved to another port, the backend's
      // FRONTEND_URL and this baseURL would disagree and every call would fail.
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      url: WEB_URL,
      env: { BACKEND_ORIGIN: API_URL },
      reuseExistingServer: !process.env.CI,
    },
    // Signed-in flows call the real API, which needs Postgres and an S3 for the ECG
    // signal up: `docker compose up -d db minio` locally, see playwright-ci.yml in CI.
    {
      command: `uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: '../back',
      url: `${API_URL}/health`,
      env: { FRONTEND_URL: WEB_URL },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
})
