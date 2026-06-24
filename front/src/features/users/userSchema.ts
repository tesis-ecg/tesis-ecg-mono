import { z } from 'zod'

/** Schema del form de alta de usuario. El rol se limita a `medico` | `admin`. */
export const userCreateSchema = z.object({
  fullName: z.string().trim().min(2, 'Ingresá el nombre completo.'),
  email: z.string().trim().email('Ingresá un email válido.'),
  password: z.string().min(8, 'La contraseña debe tener al menos 8 caracteres.'),
  role: z.enum(['medico', 'admin'], { message: 'Seleccioná un rol.' }),
})

export type UserCreateValues = z.infer<typeof userCreateSchema>

/** Schema del form de edición de email. */
export const userEmailSchema = z.object({
  email: z.string().trim().email('Ingresá un email válido.'),
})

export type UserEmailValues = z.infer<typeof userEmailSchema>
