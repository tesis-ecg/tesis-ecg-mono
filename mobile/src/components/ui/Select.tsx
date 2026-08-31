import { Check, ChevronDown } from 'lucide-react-native'
import { useState } from 'react'

import { BottomSheet } from '@/components/ui/BottomSheet'
import { Button } from '@/components/ui/Button'
import { AnimatedPressable, Pressable, Text, View } from '@/tw'
import type { CatalogOption } from '@/features/patient/types'
import * as haptics from '@/lib/haptics'
import { usePressScale } from '@/lib/motion'
import { cn } from '@/lib/cn'

interface SelectProps {
  label: string
  /** Qué se lee en el disparador cuando todavía no hay nada elegido. */
  placeholder: string
  options: CatalogOption[]
  /** Valores marcados. La selección simple guarda uno solo. */
  selected: string[]
  /**
   * La opción que se tocó, no la selección resultante.
   *
   * La regla de "no sentí nada" —que es excluyente— vive en
   * `reportSchema.toggleSymptom` y está testeada ahí. Si este componente
   * resolviera la selección, esa regla tendría dos dueños.
   */
  onSelect: (value: string) => void
  /** Deja elegir varias. Cambia el cuadrito por un círculo y deja la hoja abierta. */
  multiple?: boolean
  hint?: string
  error?: string
}

/**
 * Selector con hoja inferior.
 *
 * Las opciones no se dibujan en el formulario: cada sección muestra un solo
 * control con lo elegido y las abre en una hoja. Con las dos listas completas a
 * la vista, el formulario medía tres pantallazos y el botón de enviar no
 * aparecía nunca — el paciente no llegaba al final de algo que son dos
 * preguntas.
 *
 * La forma del indicador dice cuántas opciones se pueden marcar antes de que el
 * paciente lo intente: cuadrado donde se puede elegir más de una, círculo donde
 * la elección es única. Es la convención de siempre entre checkbox y radio, y
 * acá ahorra la línea de texto que lo explicaría.
 */
export function Select({
  label,
  placeholder,
  options,
  selected,
  onSelect,
  multiple = false,
  hint,
  error,
}: SelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const press = usePressScale(0.98)

  const chosen = options.filter((option) => selected.includes(option.value))
  // Con dos o más se muestra el conteo y no los rótulos encadenados: recortar
  // "Falta de aire, Mareo, Palpitaciones" a una línea deja al paciente viendo
  // una lista que miente sobre lo que marcó.
  const summary =
    chosen.length === 0
      ? placeholder
      : chosen.length === 1
        ? chosen[0].label
        : `${chosen.length} opciones elegidas`

  return (
    <View className="gap-2">
      <Text className="text-[15px] font-medium text-gray-700">{label}</Text>

      <AnimatedPressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityValue={{ text: chosen.length === 0 ? 'Sin elegir' : summary }}
        accessibilityState={{ expanded: isOpen }}
        onPress={() => {
          haptics.tap()
          setIsOpen(true)
        }}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        className={cn(
          'min-h-[56px] flex-row items-center gap-3 rounded-full border-2 bg-white px-4',
          error
            ? 'border-error-400'
            : chosen.length > 0
              ? 'border-primary-300'
              : 'border-gray-100',
        )}
        style={press.style}
      >
        <Text
          numberOfLines={1}
          className={cn(
            'flex-1 text-[17px]',
            chosen.length > 0 ? 'font-semibold text-gray-900' : 'text-gray-400',
          )}
        >
          {summary}
        </Text>
        <ChevronDown size={22} color="#5c6b74" />
      </AnimatedPressable>

      {error ? (
        <Text className="text-[13px] text-error-700">{error}</Text>
      ) : hint ? (
        <Text className="text-[13px] text-gray-600">{hint}</Text>
      ) : null}

      <BottomSheet
        visible={isOpen}
        onClose={() => setIsOpen(false)}
        title={label}
        footer={
          multiple ? <Button label="Listo" onPress={() => setIsOpen(false)} /> : undefined
        }
      >
        {options.map((option, index) => (
          <Option
            key={option.value}
            label={option.label}
            selected={selected.includes(option.value)}
            multiple={multiple}
            // Las variantes de hermanos de Tailwind no existen en React Native:
            // la última fila no se puede resolver con `last:`.
            divider={index < options.length - 1}
            onPress={() => {
              onSelect(option.value)
              // La elección única no tiene nada más que decidir; quedarse abierta
              // obliga a un toque de más para volver al formulario.
              if (!multiple) setIsOpen(false)
            }}
          />
        ))}
      </BottomSheet>
    </View>
  )
}

/**
 * Una fila de la hoja.
 *
 * El golpecito háptico al marcar no es adorno: marcar un síntoma es lo único
 * que el paciente hace en la app que después lee su médico, y la confirmación
 * táctil es lo que despeja la duda de si el toque entró.
 */
function Option({
  label,
  selected,
  multiple,
  divider,
  onPress,
}: {
  label: string
  selected: boolean
  multiple: boolean
  divider: boolean
  onPress: () => void
}) {
  return (
    <Pressable
      accessibilityRole={multiple ? 'checkbox' : 'radio'}
      accessibilityState={{ checked: selected }}
      accessibilityLabel={label}
      onPress={() => {
        haptics.selection()
        onPress()
      }}
      className={cn(
        'min-h-[56px] flex-row items-center gap-3 py-3',
        divider && 'border-b border-gray-100',
      )}
    >
      <View
        className={cn(
          'size-6 items-center justify-center border-2',
          multiple ? 'rounded-[7px]' : 'rounded-full',
          selected ? 'border-primary-500 bg-primary-500' : 'border-gray-300 bg-white',
        )}
      >
        {selected ? <Check size={15} color="#ffffff" strokeWidth={3.5} /> : null}
      </View>
      <Text
        className={cn(
          'flex-1 text-[17px]',
          selected ? 'font-semibold text-primary-500' : 'text-gray-900',
        )}
      >
        {label}
      </Text>
    </Pressable>
  )
}
