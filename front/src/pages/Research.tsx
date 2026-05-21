import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function Research() {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Investigación</h1>
        <p className="text-body2 text-gray-600">Datasets, ensayos y exportaciones para análisis.</p>
      </header>

      <Card className="p-6">
        <CardHeader className="p-0">
          <CardTitle className="text-h6 text-gray-900">Investigación — placeholder</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pt-2">
          <p className="text-body2 text-gray-600">
            El hub de investigación con cohortes y exportaciones se sumará en otro ticket.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
