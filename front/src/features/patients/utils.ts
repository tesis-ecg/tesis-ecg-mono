import type { PatientFormValues } from './patientSchema'
import type { CreatePatientInput, Patient } from './types'

/**
 * Convierte los valores del form (todos strings) al payload de API,
 * normalizando los campos de contacto vacíos a `null`.
 */
export function patientFormToInput(values: PatientFormValues): CreatePatientInput {
  const email = values.contactEmail.trim()
  const phone = values.contactPhone.trim()
  return {
    fullName: values.fullName.trim(),
    dni: values.dni.trim(),
    birthDate: values.birthDate,
    sex: values.sex,
    contactEmail: email === '' ? null : email,
    contactPhone: phone === '' ? null : phone,
  }
}

/**
 * Deriva los valores iniciales del form a partir de un paciente existente,
 * para precargar el diálogo de edición.
 */
export function patientToFormValues(patient: Patient): PatientFormValues {
  return {
    fullName: patient.fullName,
    dni: patient.dni,
    birthDate: patient.birthDate,
    sex: patient.sex,
    contactEmail: patient.contactEmail ?? '',
    contactPhone: patient.contactPhone ?? '',
  }
}

/**
 * Calcula la edad en años cumplidos a partir de una fecha de nacimiento ISO
 * (`YYYY-MM-DD`). Ajusta por mes/día para no contar el cumpleaños del año en
 * curso si todavía no ocurrió. Reemplaza al viejo campo `age`, que envejecía mal.
 */
export function calculateAge(birthDate: string): number {
  const birth = new Date(birthDate)
  if (Number.isNaN(birth.getTime())) return 0

  const now = new Date()
  let age = now.getFullYear() - birth.getFullYear()
  const monthDiff = now.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && now.getDate() < birth.getDate())) {
    age -= 1
  }
  return age
}
