import {
  ALERT_URGENCY_INK,
  alertMeta,
  alertUrgency,
  parseSeverity,
} from '@/features/patient/deviceMeta'
import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/format'
import { alertGradient } from '@/lib/gradients'
import { Card } from '@/components/ui/Card'
import { Body, Caption, Heading } from '@/components/ui/typography'
import { Text, View } from '@/tw'

interface DetectedBannerProps {
  /** Tipo de hallazgo. Un push viejo puede no traerlo; ahí cae en la etiqueta genérica. */
  kind?: string
  /** Severidad del aviso, como llega en la ruta. Sin ella se pinta por el tipo. */
  severity?: string
  /** Instante del hallazgo, en ISO. */
  occurredAt?: string
}

/**
 * Qué se detectó y cuándo, arriba del formulario.
 *
 * El formulario pregunta "¿cómo te sentiste?" sobre un momento que para el
 * paciente no tiene ninguna marca: el chaleco no vibra ni suena, así que el
 * aviso puede llegar veinte minutos después de algo que él no registró. Sin
 * decirle **qué** encontramos y **cuándo**, la única respuesta honesta que le
 * queda es "no sé" — y ese formulario en blanco es exactamente lo que el médico
 * no puede leer.
 *
 * Con el día y la hora enfrente, la pregunta pasa a ser contestable: a las 15:40
 * de un jueves estaba subiendo la escalera, y eso sí se acuerda.
 *
 * Lleva el mismo color, ícono y borde que el aviso en la pila de Inicio: el
 * paciente tocó una card amarilla y tiene que reconocerla acá arriba. Y es un
 * fondo claro y no un color pleno: el call-to-action de esta pantalla es
 * "Enviar a mi médico" y un bloque de color fuerte arriba de todo se lo comería.
 */
export function DetectedBanner({ kind, severity, occurredAt }: DetectedBannerProps) {
  const alertKind = kind ?? 'other'
  const meta = alertMeta(alertKind)
  const urgency = alertUrgency(alertKind, parseSeverity(severity))
  const ink = ALERT_URGENCY_INK[urgency]
  const Icon = meta.icon

  return (
    <Card className={cn('gap-3 border', ink.border)} style={alertGradient[urgency]}>
      <View className="flex-row items-center gap-3">
        <View className="size-11 items-center justify-center rounded-full bg-white">
          <Icon size={22} color={ink.icon} />
        </View>
        <View className="flex-1 gap-0.5">
          <Caption>Esto detectamos</Caption>
          <Heading className="text-[17px]">{meta.label}</Heading>
        </View>
      </View>

      <View className="gap-0.5 rounded-[16px] bg-white px-4 py-3">
        <Caption>Cuándo pasó</Caption>
        <Text className="text-[17px] font-semibold text-gray-900">
          {formatDateTime(occurredAt)}
        </Text>
      </View>

      <Body className="text-gray-700">
        Tratá de acordarte qué estabas haciendo en ese momento y cómo te sentiste. Con eso tu
        médico entiende el registro.
      </Body>
    </Card>
  )
}
