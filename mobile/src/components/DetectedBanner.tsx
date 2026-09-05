import { alertMeta } from '@/features/patient/deviceMeta'
import { formatDateTime } from '@/lib/format'
import { Card } from '@/components/ui/Card'
import { Body, Caption, Heading } from '@/components/ui/typography'
import { Text, View } from '@/tw'

interface DetectedBannerProps {
  /** Tipo de hallazgo. Un push viejo puede no traerlo; ahí cae en la etiqueta genérica. */
  kind?: string
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
 * Azul suave y no el gradiente de marca: el call-to-action de esta pantalla es
 * "Enviar a mi médico" y un bloque de color pleno arriba de todo se lo comería.
 */
export function DetectedBanner({ kind, occurredAt }: DetectedBannerProps) {
  const meta = alertMeta(kind ?? 'other')
  const Icon = meta.icon

  return (
    <Card className="gap-3 bg-primary-50">
      <View className="flex-row items-center gap-3">
        <View className="size-11 items-center justify-center rounded-full bg-white">
          <Icon size={22} color="#0b2185" />
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
