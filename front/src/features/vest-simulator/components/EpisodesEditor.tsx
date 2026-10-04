import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import { EPISODE_META, type Episode, type EpisodeKind } from '../codec/signal'
import { makeEpisode } from '../defaults'

interface EpisodesEditorProps {
  episodes: Episode[]
  batchCount: number
  batchMinutes: number
  onChange: (episodes: Episode[]) => void
}

const KINDS = Object.keys(EPISODE_META) as EpisodeKind[]

/**
 * Lista editable de episodios agendados: qué pasa, en qué lote de la corrida y
 * en qué segundo de ese lote. Un episodio que se pasa del final del lote sigue
 * en el siguiente.
 */
export function EpisodesEditor({
  episodes,
  batchCount,
  batchMinutes,
  onChange,
}: EpisodesEditorProps) {
  const update = (id: string, changes: Partial<Episode>) =>
    onChange(episodes.map((episode) => (episode.id === id ? { ...episode, ...changes } : episode)))

  return (
    <div className="flex flex-col gap-3 sm:col-span-2">
      {episodes.length === 0 && (
        <p className="text-body3 text-gray-600">
          Sin episodios: ritmo sinusal con su variabilidad normal.
        </p>
      )}
      {episodes.map((episode) => {
        const meta = EPISODE_META[episode.kind]
        const outOfRun = episode.batch > batchCount
        const beyondBatch = episode.startSec >= batchMinutes * 60
        return (
          <div
            key={episode.id}
            className="grid grid-cols-2 gap-2 rounded-md border border-gray-200 p-3 sm:grid-cols-[1.6fr_0.6fr_0.8fr_0.8fr_1fr_auto] sm:items-end"
          >
            <div className="col-span-2 flex flex-col gap-1 sm:col-span-1">
              <Label className="text-body3">Episodio</Label>
              <Select
                value={episode.kind}
                onValueChange={(kind) =>
                  update(episode.id, {
                    kind: kind as EpisodeKind,
                    value: EPISODE_META[kind as EpisodeKind].defaultValue,
                    durationSec: EPISODE_META[kind as EpisodeKind].defaultDurationSec,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KINDS.map((kind) => (
                    <SelectItem key={kind} value={kind}>
                      {EPISODE_META[kind].label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Lote</Label>
              <Input
                type="number"
                min={1}
                max={batchCount}
                value={episode.batch}
                onChange={(event) =>
                  update(episode.id, { batch: Math.max(1, Number(event.target.value) || 1) })
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Desde (s)</Label>
              <Input
                type="number"
                min={0}
                value={episode.startSec}
                onChange={(event) =>
                  update(episode.id, { startSec: Math.max(0, Number(event.target.value) || 0) })
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Dura (s)</Label>
              <Input
                type="number"
                min={0}
                value={meta.instant ? '' : episode.durationSec}
                placeholder={meta.instant ? 'puntual' : undefined}
                disabled={meta.instant}
                onChange={(event) =>
                  update(episode.id, { durationSec: Math.max(0, Number(event.target.value) || 0) })
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-body3">{meta.valueLabel ?? 'Parámetro'}</Label>
              <Input
                type="number"
                min={0}
                value={meta.valueLabel ? episode.value : ''}
                placeholder={meta.valueLabel ? undefined : '—'}
                disabled={!meta.valueLabel}
                onChange={(event) =>
                  update(episode.id, { value: Math.max(0, Number(event.target.value) || 0) })
                }
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-red-600"
              onClick={() => onChange(episodes.filter((item) => item.id !== episode.id))}
              aria-label={`Quitar ${meta.label}`}
            >
              <Trash2 className="size-4" aria-hidden />
            </Button>
            {(outOfRun || beyondBatch) && (
              <p className="col-span-full text-body3 text-amber-700">
                {outOfRun
                  ? `La corrida tiene ${batchCount} lote(s): este episodio no va a ocurrir.`
                  : `Arranca después del final del lote (${batchMinutes * 60} s): cae en el siguiente.`}
              </p>
            )}
          </div>
        )
      })}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...episodes, makeEpisode('pvc')])}
      >
        <Plus className="mr-1 size-4" aria-hidden />
        Agregar episodio
      </Button>
    </div>
  )
}
