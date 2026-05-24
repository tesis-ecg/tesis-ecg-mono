import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'

export function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white px-6 text-center">
      <span className="text-h2 font-semibold text-primary-500">404</span>
      <h1 className="text-h4 text-gray-900">Página no encontrada</h1>
      <p className="max-w-md text-body2 text-gray-600">
        La ruta que intentaste abrir no existe o fue movida.
      </p>
      <Button asChild className="mt-2 bg-primary-500 text-white hover:bg-primary-600">
        <Link to="/">Volver al inicio</Link>
      </Button>
    </main>
  )
}
