import { useParams } from 'react-router-dom'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function StudyDetail() {
  const { id } = useParams<{ id: string }>()
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Estudio {id}</h1>
        <p className="text-body2 text-gray-600">Visualizador de ECG — placeholder.</p>
      </header>
      <Card className="p-6">
        <CardHeader className="p-0">
          <CardTitle className="text-h6 text-gray-900">Datos del estudio</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pt-2">
          <p className="text-body2 text-gray-600">
            La visualización del ECG se implementa en un ticket posterior.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
