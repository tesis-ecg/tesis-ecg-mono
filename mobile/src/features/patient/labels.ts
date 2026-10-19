/**
 * Etiquetas legibles de los slugs del catálogo.
 *
 * Los catálogos los sirve el backend (`GET /mobile/catalogs`) y la pantalla los
 * mapea con ellos. Este módulo cubre los dos huecos que deja ese mapa:
 *
 * - **Mientras el catálogo carga.** El historial se dibuja antes de que llegue
 *   la respuesta, y el fallback `?? slug` mostraba `dolor_pecho` en pantalla.
 * - **Los registros viejos.** Si un slug se retira del catálogo, los que ya se
 *   guardaron tienen que seguir siendo legibles.
 *
 * Es el mismo `_humanize` del backend (`app/modules/patient_app/catalogs.py`),
 * portado para que las dos puntas degraden igual.
 */

/** `dolor_pecho` → `Dolor pecho`. Feo, pero legible; `dolor_pecho` no lo es. */
export function humanize(value: string): string {
  const readable = value.replace(/_/g, ' ').trim()
  if (!readable) return value
  return readable[0].toUpperCase() + readable.slice(1)
}

/** La etiqueta del catálogo si llegó, y si no una versión legible del slug. */
export function labelFor(labels: Map<string, string>, value: string): string {
  return labels.get(value) || humanize(value)
}
