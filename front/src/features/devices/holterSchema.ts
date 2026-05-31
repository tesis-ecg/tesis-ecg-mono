import { z } from 'zod'

/**
 * Schema del form de alta de Holter. `firmwareVersion` admite vacío y se
 * normaliza a `null` al construir el payload en `holterFormToInput`.
 */
export const holterFormSchema = z.object({
  serial: z
    .string()
    .trim()
    .regex(/^HOLTER-AR-\d{3,}$/, 'Formato esperado: HOLTER-AR-NNN (mín. 3 dígitos).'),
  model: z.string().trim().min(2, 'Ingresá el modelo del dispositivo.'),
  firmwareVersion: z.string().trim(),
})

export type HolterFormValues = z.infer<typeof holterFormSchema>

/**
 * Schema del form de edición. Serial es inmutable; status sólo se puede mover
 * entre `available` y `maintenance` desde el form (los otros son resultado de
 * acciones: assign/unassign y delete).
 */
export const holterEditSchema = z.object({
  model: z.string().trim().min(2, 'Ingresá el modelo del dispositivo.'),
  firmwareVersion: z.string().trim(),
  status: z.enum(['available', 'maintenance']),
})

export type HolterEditValues = z.infer<typeof holterEditSchema>
