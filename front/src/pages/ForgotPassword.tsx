import { Link } from 'react-router-dom'

export function ForgotPassword() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white px-6 text-center">
      <h1 className="text-h4 text-gray-900">Recuperar contraseña</h1>
      <p className="max-w-md text-body2 text-gray-600">
        Esta función todavía no está disponible. Si necesitás restablecer tu contraseña, contactá al
        administrador de tu institución.
      </p>
      <Link to="/login" className="text-body2 text-primary-500 hover:underline">
        Volver al login
      </Link>
    </main>
  )
}
