import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function Studies() {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Estudios</h1>
        <p className="text-body2 text-gray-600">Registros ECG y sesiones de monitoreo.</p>
      </header>

      <Card className="p-6">
        <CardHeader className="p-0">
          <CardTitle className="text-h6 text-gray-900">Estudios — placeholder</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pt-2">
          <p className="text-body2 text-gray-600">
            El visor de ECG y el detalle de estudios se sumarán en otro ticket.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
