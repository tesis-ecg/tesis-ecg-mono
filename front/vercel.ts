import { routes, type VercelConfig } from '@vercel/config/v1'

function requiredHttpsOrigin(name: string): string {
  const rawValue = process.env[name]
  if (!rawValue) throw new Error(`${name} es obligatorio para desplegar en Vercel.`)

  const url = new URL(rawValue)
  if (url.protocol !== 'https:' || url.pathname !== '/' || url.search || url.hash) {
    throw new Error(`${name} debe ser un origen HTTPS sin path, query ni fragment.`)
  }
  return url.origin
}

const backendOrigin = requiredHttpsOrigin('BACKEND_ORIGIN')

export const config: VercelConfig = {
  framework: 'vite',
  outputDirectory: 'dist',
  rewrites: [
    routes.rewrite('/api/:path*', `${backendOrigin}/:path*`),
    routes.rewrite('/(.*)', '/index.html'),
  ],
  headers: [
    {
      source: '/api/:path*',
      headers: [{ key: 'Cache-Control', value: 'private, no-store' }],
    },
    {
      source: '/(.*)',
      headers: [
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
        {
          key: 'Content-Security-Policy-Report-Only',
          value:
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' data: blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; worker-src 'self'; connect-src 'self' https:",
        },
      ],
    },
  ],
}
