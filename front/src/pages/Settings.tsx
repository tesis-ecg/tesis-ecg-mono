import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function Settings() {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Configuración</h1>
        <p className="text-body2 text-gray-600">Preferencias de cuenta y del sistema.</p>
      </header>

      <Card className="p-6">
        <CardHeader className="p-0">
          <CardTitle className="text-h6 text-gray-900">Configuración — placeholder</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pt-2">
          <p className="text-body2 text-gray-600">
            Las opciones de cuenta, equipo y notificaciones se sumarán en otro ticket.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
