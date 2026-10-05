import path from 'node:path'
import { configDefaults, defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    // Los `*.spec.ts` de e2e/ son de Playwright: Vitest los recogería por el patrón
    // por defecto y fallarían al importar `@playwright/test`.
    exclude: [...configDefaults.exclude, 'e2e/**'],
    environment: 'node',
    coverage: {
      provider: 'v8',
      include: ['src/features/auth/safeRedirect.ts', 'src/lib/apiError.ts'],
      reporter: ['text', 'json-summary'],
      thresholds: {
        lines: 70,
        functions: 70,
        statements: 70,
        branches: 70,
      },
    },
  },
})
