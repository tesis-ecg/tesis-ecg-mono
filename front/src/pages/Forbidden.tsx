import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'

export function Forbidden() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white px-6 text-center">
      <span className="text-h2 font-semibold text-primary-500">403</span>
      <h1 className="text-h4 text-gray-900">Acceso denegado</h1>
      <p className="max-w-md text-body2 text-gray-600">
        Tu rol no tiene permisos para acceder a esta sección. Contactá a tu administrador si creés
        que es un error.
      </p>
      <Button asChild className="mt-2 bg-primary-500 text-white hover:bg-primary-600">
        <Link to="/">Volver al inicio</Link>
      </Button>
    </main>
  )
}
