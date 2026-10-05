/**
 * Señal ECG del simulador: el modelo vive en `ecgModel/`.
 *
 * El generador anterior quedó congelado en `legacySignal.ts`, solo para el
 * fixture dorado del codec.
 */

export {
  generateEcg,
  initialGeneratorState,
  isGeneratorState,
  FULL_SCALE_UV,
  SATURATION_UV,
  type GeneratedEcg,
  type GenerateRequest,
  type GeneratorState,
} from './ecgModel/generator'
export {
  DEFAULT_SIGNAL_PROFILE,
  EPISODE_META,
  type ElectrodeKind,
  type Episode,
  type EpisodeKind,
  type MainsEnvironment,
  type ResolvedEpisode,
  type SignalProfile,
} from './ecgModel/types'
