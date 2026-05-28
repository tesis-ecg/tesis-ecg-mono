import { z } from 'zod'

/**
 * Schema compartido entre el alta y la edición de pacientes.
 *
 * Todos los campos son strings (lo que producen los inputs del form). Los
 * campos de contacto admiten vacío y se normalizan a `null` al construir el
 * payload (`CreatePatientInput`) en los diálogos.
 */
export const patientFormSchema = z.object({
  fullName: z.string().trim().min(2, 'Ingresá el nombre completo (mín. 2 caracteres).'),
  dni: z
    .string()
    .trim()
    .regex(/^\d{8}$/, 'El DNI debe tener 8 dígitos numéricos.'),
  birthDate: z
    .string()
    .min(1, 'Ingresá la fecha de nacimiento.')
    .refine((v) => !Number.isNaN(new Date(v).getTime()), 'Fecha inválida.')
    .refine((v) => new Date(v).getFullYear() > 1900, 'El año debe ser posterior a 1900.')
    .refine((v) => new Date(v) <= new Date(), 'La fecha de nacimiento no puede ser futura.'),
  sex: z.enum(['M', 'F', 'X'], 'Seleccioná un sexo.'),
  contactEmail: z.email('Ingresá un email válido.').or(z.literal('')),
  contactPhone: z.string().trim(),
})

export type PatientFormValues = z.infer<typeof patientFormSchema>
