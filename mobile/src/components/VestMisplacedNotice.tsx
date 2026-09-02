import { TriangleAlert } from 'lucide-react-native'

import { Body } from '@/components/ui/typography'
import { View } from '@/tw'

/**
 * El cartel de "acomodate el chaleco".
 *
 * Está en un componente y no copiado en las dos pantallas porque el texto es lo
 * único que el paciente tiene para saber qué hacer, y dos copias se
 * desincronizan: la de Inicio se ajusta, la de Dispositivo se queda con la
 * redacción vieja, y el mismo problema termina explicado de dos maneras
 * distintas en la misma app.
 *
 * Dice el problema y la acción, y nada más. El motivo —que sin contacto con la
 * piel la señal no sirve— es cierto pero no cambia lo que hay que hacer, y
 * alargar el cartel hace que se lea menos.
 */
export function VestMisplacedNotice() {
  return (
    <View className="flex-row gap-3 rounded-lg bg-error-100 p-3">
      <TriangleAlert size={22} color="#88271d" />
      <Body className="flex-1 text-error-700">
        Tu chaleco está mal colocado y está midiendo mal. Acomodátelo sobre el pecho hasta que
        quede ajustado contra la piel.
      </Body>
    </View>
  )
}
