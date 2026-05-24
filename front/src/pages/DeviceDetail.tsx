import { useParams } from 'react-router-dom'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function DeviceDetail() {
  const { id } = useParams<{ id: string }>()
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Dispositivo {id}</h1>
        <p className="text-body2 text-gray-600">Detalle de dispositivo — placeholder.</p>
      </header>
      <Card className="p-6">
        <CardHeader className="p-0">
          <CardTitle className="text-h6 text-gray-900">Estado del dispositivo</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pt-2">
          <p className="text-body2 text-gray-600">El detalle se implementa en otro ticket.</p>
        </CardContent>
      </Card>
    </div>
  )
}
