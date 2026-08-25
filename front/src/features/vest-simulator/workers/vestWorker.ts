/**
 * Entrypoint del worker de generación de lotes.
 *
 * Una hora de señal son ~1,8 M de muestras y ~8.600 tramas: comprimirlas en el
 * hilo principal congelaría la UI durante segundos, y con varios chalecos a la
 * vez la página quedaría inutilizable. El worker devuelve los bytes ya listos
 * para subir; el upload lo hace el hilo principal, así N chalecos suben en
 * paralelo.
 *
 * Acá solo va la plomería de mensajes: la lógica está en
 * `../codec/batchBuilder.ts`, que es un módulo puro y testeable.
 */

import { buildBatch, type VestWorkerRequest } from '../codec/batchBuilder'

self.onmessage = (event: MessageEvent<VestWorkerRequest>) => {
  const response = buildBatch(event.data)
  self.postMessage(response, [response.body])
}
