// eslint-disable-next-line @typescript-eslint/no-require-imports
const { getDefaultConfig } = require('expo/metro-config')
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { withNativewind } = require('nativewind/metro')

/** @type {import('expo/metro-config').MetroConfig} */
const config = getDefaultConfig(__dirname)

module.exports = withNativewind(config, {
  // Inlinear variables rompe `platformColor()` dentro de variables CSS.
  inlineVariables: false,
  // El soporte de `className` se agrega a mano en `src/tw`.
  globalClassNamePolyfill: false,
})
