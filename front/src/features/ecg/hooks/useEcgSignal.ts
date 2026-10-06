import { useQuery } from '@tanstack/react-query'

import { getStudyEcg } from '../api/ecgApi'
import type { ECGSignal } from '../types'

/** Cada cuánto se vuelve a pedir el manifest de un estudio en curso. */
const IN_PROGRESS_POLL_MS = 60_000

/**
 * Trae la señal ECG de un estudio.
 *
 * Un estudio **terminado** es inmutable: `staleTime: Infinity` y no se
 * refetchea nunca. Uno **en curso** no lo es — el chaleco sube un lote por hora
 * y la pirámide se rehace en cada uno, así que la señal crece mientras el
 * médico la mira. Ahí conviene volver a pedirla cada tanto.
 *
 * Un minuto de intervalo contra lotes horarios puede parecer poco, pero un
 * refetch es un GET al manifest más la descarga de un nivel de pirámide (unos
 * cientos de kB), y hace que el gráfico se actualice sin que nadie recargue la
 * página.
 *
 * `gcTime` corto para que el `Float32Array` se libere al desmontar.
 *
 * Cada respuesta trae arrays tipados nuevos, que el structural sharing de React
 * Query no sabe comparar: sin `keepSameSignal`, cada refetch (el intervalo, o
 * volver a la pestaña) entregaba otra referencia y el visor recreaba el gráfico
 * aunque no hubiera llegado ni una muestra.
 */
export function useEcgSignal(studyId: string | undefined, isInProgress = false) {
  return useQuery({
    queryKey: ['ecg', studyId],
    queryFn: ({ signal }) => getStudyEcg(studyId!, signal),
    enabled: Boolean(studyId),
    staleTime: isInProgress ? 0 : Infinity,
    refetchInterval: isInProgress ? IN_PROGRESS_POLL_MS : false,
    gcTime: 60 * 1000,
    structuralSharing: (previous, next) => keepSameSignal(previous, next),
  })
}

function keepSameSignal(previous: unknown, next: unknown): unknown {
  const before = previous as ECGSignal | undefined
  const after = next as ECGSignal
  if (!before || !after) return next
  const same =
    before.startTimestamp === after.startTimestamp &&
    before.durationMs === after.durationMs &&
    before.sampleRate === after.sampleRate &&
    before.samples.length === after.samples.length &&
    before.timestampsMs.at(-1) === after.timestampsMs.at(-1) &&
    JSON.stringify(before.annotations) === JSON.stringify(after.annotations) &&
    JSON.stringify(before.metadata) === JSON.stringify(after.metadata)
  return same ? previous : next
}
