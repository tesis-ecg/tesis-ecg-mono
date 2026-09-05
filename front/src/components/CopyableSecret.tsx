import { Check, Copy, Eye, EyeOff } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CopyableSecretProps {
  /** `null` mientras el valor todavía no se pidió o está cargando. */
  value: string | null
  /** Etiqueta accesible del botón de copiar. */
  label?: string
  /**
   * Agrega un botón de ver/ocultar y arranca oculto. Para secretos que se
   * pueden releer y que por eso conviven con el resto de la pantalla, a
   * diferencia de los que se entregan una sola vez en un diálogo.
   */
  maskable?: boolean
  /** Arranca revelado aunque sea enmascarable. Para un secreto recién generado. */
  defaultRevealed?: boolean
  loading?: boolean
  /** Se llama al revelar el valor cuando todavía no está cargado. */
  onRequestValue?: () => void
  className?: string
}

/** Ancho fijo: el placeholder no tiene que delatar el largo del secreto. */
const MASK = '•'.repeat(24)

/**
 * Un secreto que se muestra en pantalla para copiarlo o dictarlo: la
 * contraseña inicial de un paciente, la API key de un equipo.
 *
 * Se muestra en monoespaciada y con `tracking` ancho a propósito: el médico se
 * lo dicta al paciente por teléfono, y a 8 caracteres la diferencia entre `rn`
 * y `m` importa.
 */
export function CopyableSecret({
  value,
  label = 'Copiar',
  maskable = false,
  defaultRevealed = false,
  loading = false,
  onRequestValue,
  className,
}: CopyableSecretProps) {
  const [copied, setCopied] = useState(false)
  const [revealed, setRevealed] = useState(!maskable || defaultRevealed)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const handleCopy = async () => {
    if (value === null) return
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      // `navigator.clipboard` no existe fuera de contextos seguros (HTTP en una
      // IP de la red del consultorio, por ejemplo). El valor sigue visible y
      // seleccionable, así que no hay nada que romper ni que avisar.
      setCopied(false)
    }
  }

  const handleToggle = () => {
    if (!revealed && value === null) onRequestValue?.()
    setRevealed((current) => !current)
  }

  const showsValue = revealed && value !== null

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3',
        className,
      )}
    >
      <code
        className={cn(
          'font-mono text-h6 tracking-[0.25em] break-all',
          showsValue ? 'text-gray-900 select-all' : 'text-gray-500 select-none',
        )}
      >
        {showsValue ? value : loading ? 'Cargando…' : MASK}
      </code>
      <div className="flex shrink-0 items-center gap-2">
        {maskable && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleToggle}
            aria-label={revealed ? 'Ocultar' : 'Mostrar'}
          >
            {revealed ? (
              <EyeOff className="size-4" aria-hidden />
            ) : (
              <Eye className="size-4" aria-hidden />
            )}
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void handleCopy()}
          aria-label={label}
          // Antes de revelar todavía no hay nada que copiar. Mantener el botón
          // habilitado hacía que el primer click solo iniciara la consulta y no
          // copiara nada, aunque su etiqueta afirmara lo contrario.
          disabled={loading || value === null}
        >
          {copied ? (
            <>
              <Check className="mr-1 size-4" aria-hidden />
              Copiado
            </>
          ) : (
            <>
              <Copy className="mr-1 size-4" aria-hidden />
              {label}
            </>
          )}
        </Button>
      </div>
    </div>
  )
}
