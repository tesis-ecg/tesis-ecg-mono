import { z } from 'zod'

/**
 * Reglas del formulario de la bitácora.
 *
 * Están acá y no dentro de la pantalla porque son las mismas que valida el
 * backend (`patient_app_schemas.MobileReportCreateRequest` y
 * `patient_app_service._validate_free_text`). Duplicarlas en el cliente no es
 * redundancia: evita que el paciente mande el formulario y reciba un 422 en
 * castellano de servidor cuando el error se podía señalar sobre el campo.
 */

/** Excluyente: junto a un síntoma da un registro que el médico no puede leer. */
export const NO_SYMPTOMS = 'sin_sintomas'
export const OTHER = 'otro'

export const reportSchema = z
  .object({
    symptoms: z.array(z.string()).min(1, 'Elegí al menos una opción de cómo te sentiste.'),
    symptomsOther: z.string(),
    activity: z.string().min(1, 'Elegí qué estabas haciendo.'),
    activityOther: z.string(),
    notes: z.string(),
  })
  .refine((value) => !(value.symptoms.includes(NO_SYMPTOMS) && value.symptoms.length > 1), {
    message: '"No sentí nada" no se combina con otros síntomas.',
    path: ['symptoms'],
  })
  .refine((value) => !value.symptoms.includes(OTHER) || value.symptomsOther.trim().length > 0, {
    message: 'Contanos qué sentiste.',
    path: ['symptomsOther'],
  })
  .refine((value) => value.activity !== OTHER || value.activityOther.trim().length > 0, {
    message: 'Contanos qué estabas haciendo.',
    path: ['activityOther'],
  })

export type ReportFormValues = z.infer<typeof reportSchema>

/** El primer mensaje de error, o `null` si el formulario está completo. */
export function validateReport(values: ReportFormValues): string | null {
  const result = reportSchema.safeParse(values)
  return result.success ? null : (result.error.issues[0]?.message ?? 'Revisá los datos cargados.')
}

/**
 * Alterna un síntoma respetando la exclusividad de "no sentí nada".
 *
 * Es una función pura y no lógica dentro del `setState` para poder probarla:
 * la regla de exclusividad se rompe de formas silenciosas (marcar el excluyente
 * y después otro, o al revés) que un test cubre y una revisión visual no.
 */
export function toggleSymptom(current: string[], value: string): string[] {
  if (value === NO_SYMPTOMS) {
    return current.includes(value) ? [] : [value]
  }
  const rest = current.filter((item) => item !== NO_SYMPTOMS)
  return rest.includes(value) ? rest.filter((item) => item !== value) : [...rest, value]
}
