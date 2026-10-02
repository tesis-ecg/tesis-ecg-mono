import type { LucideIcon } from 'lucide-react-native'
import { forwardRef, useCallback, useRef, useState, type ReactNode } from 'react'
import type { TextInput as RNTextInput } from 'react-native'

import { Pressable, Text, TextInput, View, type TextInputProps } from '@/tw'
import { cn } from '@/lib/cn'

interface FieldProps extends TextInputProps {
  label: string
  error?: string
  hint?: string
  /** Ícono adentro del campo, antes del texto. Ancla visualmente qué se pide. */
  leadingIcon?: LucideIcon
  /** Control al final del campo: el ojo de la contraseña, por ejemplo. */
  trailingAccessory?: ReactNode
}

/**
 * Campo de texto.
 *
 * El label queda visible arriba y no se reemplaza por el placeholder. Un
 * placeholder desaparece apenas se empieza a escribir, y el paciente que se
 * detiene a mitad de un formulario pierde la referencia de qué iba en ese
 * campo — con 40-70 años como público, eso pasa más de lo que se cree. El ícono
 * de adentro aporta el reconocimiento rápido sin pagar ese costo.
 *
 * El borde cambia con el foco: en un teclado abierto que tapa media pantalla,
 * es lo que dice en cuál de los dos campos se está escribiendo.
 *
 * **El cuadro entero enfoca.** El `TextInput` mide lo que mide su línea de
 * texto —unos 22 pt— y vive centrado dentro de una caja de 56: tocando arriba,
 * abajo o sobre el ícono no pasaba nada, y había que apuntar a la franja del
 * medio para empezar a escribir. Con un público de 40 a 70 años eso es un campo
 * que no responde. El `Pressable` de afuera recoge esos toques y los reenvía al
 * input, que sigue atendiendo los suyos.
 */
export const Field = forwardRef<RNTextInput, FieldProps>(function Field(
  {
    label,
    error,
    hint,
    leadingIcon: LeadingIcon,
    trailingAccessory,
    className,
    multiline,
    onFocus,
    onBlur,
    ...props
  },
  ref,
) {
  const [isFocused, setIsFocused] = useState(false)
  const inputRef = useRef<RNTextInput | null>(null)

  // El ref propio convive con el que venga de afuera: `login` no pasa ninguno,
  // pero el foco programático del cuadro necesita uno igual.
  const attachRef = useCallback(
    (node: RNTextInput | null) => {
      inputRef.current = node
      if (typeof ref === 'function') ref(node)
      else if (ref) ref.current = node
    },
    [ref],
  )

  return (
    <View className="gap-2">
      <Text className="text-[15px] font-medium text-gray-700">{label}</Text>

      <Pressable
        // Sin esto VoiceOver anunciaría un botón por delante del campo; el
        // elemento accesible tiene que seguir siendo el `TextInput`.
        accessible={false}
        onPress={() => inputRef.current?.focus()}
        className={cn(
          'flex-row gap-3 border-2 bg-white px-4',
          // Con varias líneas el ícono se alinea arriba, con el principio del
          // texto; centrado quedaría flotando a media altura del cuadro. Y el
          // cuadro deja de ser una píldora: un `rounded-full` sobre una caja
          // alta curva tanto los lados que el texto arranca contra la panza del
          // borde. La píldora es de los campos de una línea.
          multiline ? 'items-start rounded-[20px] py-3' : 'min-h-[56px] items-center rounded-full',
          error
            ? 'border-error-400'
            : isFocused
              ? 'border-primary-300'
              : 'border-gray-100',
        )}
      >
        {LeadingIcon ? (
          <LeadingIcon
            size={20}
            color={error ? '#ec7f74' : isFocused ? '#0b2185' : '#89939b'}
            // Acompaña al texto en la primera línea cuando el campo crece.
            style={multiline ? { marginTop: 3 } : undefined}
          />
        ) : null}

        <TextInput
          ref={attachRef}
          accessibilityLabel={label}
          placeholderTextColor="#a0a8ae"
          multiline={multiline}
          // Android centra verticalmente el texto de un `TextInput` multilínea;
          // iOS lo arranca arriba. El campo de "contanos qué sentiste" mide 88 pt
          // y en Android el placeholder flotaba a media altura, como si el cuadro
          // fuera un campo de una línea muy alto — y al escribir el texto seguía
          // centrado, corriéndose solo al llegar a la tercera línea.
          textAlignVertical={multiline ? 'top' : undefined}
          onFocus={(event) => {
            setIsFocused(true)
            onFocus?.(event)
          }}
          onBlur={(event) => {
            setIsFocused(false)
            onBlur?.(event)
          }}
          className={cn(
            'flex-1 text-[17px] text-gray-900',
            // El `EditText` de Android trae su propio padding, heredado del
            // drawable de fondo, que se suma al `py-3` del cuadro: aun alineado
            // arriba, el texto arrancaba 13 dp más abajo que en iOS. Tiene que
            // ser `p-0` y no `py-0` — medido: con solo los verticales el padding
            // del drawable sobrevive y el hueco no se mueve ni un pixel. El aire
            // lo pone el contenedor, que es el que sabe cuánto mide el campo.
            multiline && 'p-0',
            className,
          )}
          {...props}
        />

        {trailingAccessory}
      </Pressable>

      {error ? (
        <Text className="text-[13px] text-error-700">{error}</Text>
      ) : hint ? (
        <Text className="text-[13px] text-gray-600">{hint}</Text>
      ) : null}
    </View>
  )
})
