import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CopyableSecretProps {
  value: string
  /** Etiqueta accesible del botón. */
  label?: string
  className?: string
}

/**
 * Un secreto que se entrega una sola vez: contraseña inicial de un paciente,
 * API key de un equipo.
 *
 * Se muestra en monoespaciada y con `tracking` ancho a propósito: el médico se
 * lo dicta al paciente por teléfono, y a 8 caracteres la diferencia entre `rn`
 * y `m` importa.
 */
export function CopyableSecret({ value, label = 'Copiar', className }: CopyableSecretProps) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const handleCopy = async () => {
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

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3',
        className,
      )}
    >
      <code className="font-mono text-h6 tracking-[0.25em] break-all text-gray-900 select-all">
        {value}
      </code>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleCopy()}
        aria-label={label}
        className="shrink-0"
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
  )
}
