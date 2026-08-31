import path from 'node:path'
import { defineConfig } from 'vitest/config'

/**
 * Solo lógica pura.
 *
 * No hay tests de render: montar componentes de React Native exige el preset de
 * Jest de Expo, que no convive con Vitest. Lo que se prueba acá es lo que se
 * rompe en silencio — validación del formulario, a dónde lleva cada
 * notificación, formateo de fechas — y eso no necesita renderizar nada.
 */
export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
