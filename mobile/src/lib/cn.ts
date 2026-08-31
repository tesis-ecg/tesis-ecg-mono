import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Mismo helper que el portal: compone clases condicionales sin duplicar utilities. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
