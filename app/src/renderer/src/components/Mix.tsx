import { useEffect, useState } from 'react'
import { api, type Match } from '../api'
import type { Job, Song } from '../types'

interface Props {
  song: Song
  busy: boolean
  onMashup: (vocals: string, instrumental: string, opts?: { semitones?: number | null; offset_bars?: number; song_id?: string }) => void
  onOpen: (id: string) => void
  jobs: Job[]
}

const signed = (n: number): string => (n > 0 ? `+${n}` : `${n}`)

export function Mix({ song, busy, onMashup, onOpen, jobs }: Props) {
  if (song.kind === 'mashup' && song.mashup) return <MashupPanel song={song} busy={busy} onMashup={onMashup} onOpen={onOpen} />
  return <Matches song={song} onMashup={onMashup} onOpen={onOpen} jobs={jobs} />
}

function MashupPanel({ song, busy, onMashup, onOpen }: Omit<Props, 'jobs'>) {
  const m = song.mashup!
  const rerender = (patch: { semitones?: number | null; offset_bars?: number }): void =>
    onMashup(m.vocals_from.id, m.instrumental_from.id, {
      semitones: m.semitones,
      offset_bars: m.offset_bars,
      ...patch,
      song_id: song.id
    })
  return (
    <div className="mix">
      <div className="mash-sources">
        <div className="mash-card" onClick={() => onOpen(m.vocals_from.id)}>
          <div className="field-label">Vocals from</div>
          <div className="card-big">{m.vocals_from.title}</div>
          <div className="muted">{Math.round(m.vocals_from.tempo)} BPM · {m.vocals_from.key}</div>
        </div>
        <div className="mash-x">×</div>
        <div className="mash-card" onClick={() => onOpen(m.instrumental_from.id)}>
          <div className="field-label">Instrumental from</div>
          <div className="card-big">{m.instrumental_from.title}</div>
          <div className="muted">{Math.round(m.instrumental_from.tempo)} BPM · {m.instrumental_from.key}</div>
        </div>
      </div>

      <div className="glance">
        <div className="glance-item">
          <div className="field-label">Tempo</div>
          <div className="field-value">Vocals stretched ×{m.stretch.toFixed(3)}</div>
          <div className="muted">{signed(Math.round((m.stretch - 1) * 1000) / 10)}% to hit {Math.round(m.instrumental_from.tempo)} BPM</div>
        </div>
        <div className="glance-item">
          <div className="field-label">Key</div>
          <div className="field-value">{m.semitones === 0 ? 'No shift needed' : `Vocals shifted ${signed(m.semitones)} semitones`}</div>
          <div className="muted">{m.vocal_key_after ? `Now in ${m.vocal_key_after}` : ''}</div>
          <div className="btns tight">
            <button disabled={busy} onClick={() => rerender({ semitones: m.semitones - 1 })}>−1</button>
            <button disabled={busy} onClick={() => rerender({ semitones: null })}>Auto</button>
            <button disabled={busy} onClick={() => rerender({ semitones: m.semitones + 1 })}>+1</button>
          </div>
        </div>
        <div className="glance-item">
          <div className="field-label">Timing</div>
          <div className="field-value">
            {m.offset_bars === 0 ? 'Auto-aligned' : `${signed(m.offset_bars)} bar${Math.abs(m.offset_bars) === 1 ? '' : 's'} from auto`}
          </div>
          <div className="muted">Vocal entry lands on the instrumental's first section</div>
          <div className="btns tight">
            <button disabled={busy} onClick={() => rerender({ offset_bars: m.offset_bars - 1 })}>← 1 bar</button>
            <button disabled={busy} onClick={() => rerender({ offset_bars: 0 })}>Auto</button>
            <button disabled={busy} onClick={() => rerender({ offset_bars: m.offset_bars + 1 })}>1 bar →</button>
          </div>
        </div>
      </div>

      <div className="muted small">
        {m.engine === 'rubberband' && 'Stretched with Rubber Band.'}
        {m.engine === 'signalsmith' && (
          <>
            Stretched with Signalsmith Stretch. Optional:{' '}
            <a href="https://breakfastquay.com/rubberband/" target="_blank" rel="noreferrer">Rubber Band</a> on your PATH is
            used instead when present.
          </>
        )}
        {m.engine === 'librosa' && (
          <>
            Stretched with librosa's fallback, which can sound phasey: Signalsmith Stretch isn't installed. Use{' '}
            <strong>Settings → Python environment</strong> to run setup again, then re-render.
          </>
        )}
      </div>
    </div>
  )
}

function Matches({ song, onMashup, onOpen, jobs }: Omit<Props, 'busy'>) {
  const [list, setList] = useState<Match[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const libraryVersion = jobs.filter((j) => j.status === 'done').length

  useEffect(() => {
    api.matches(song.id).then(setList).catch((e) => setError((e as Error).message))
  }, [song.id, libraryVersion])

  const hasVocals = !!song.lyrics?.lines.length
  if (error) return <div className="empty-panel">{error}</div>
  if (!list) return <div className="empty-panel">Finding songs that mix well…</div>
  if (!list.length) return <div className="empty-panel">Analyse a few more songs to find mashup partners for this one.</div>

  return (
    <div className="mix">
      <div className="muted">
        Ranked by tempo (how much stretching), key (Camelot wheel distance), energy and shared story themes.
        A mashup puts one song's vocals over the other's instrumental, matched in tempo and key.
      </div>
      {list.map((m) => (
        <div key={m.id} className="match">
          <div className="match-score" title="Overall compatibility">
            <span style={{ height: `${m.score * 100}%` }} />
          </div>
          <div className="match-main">
            <div className="match-title" onClick={() => onOpen(m.id)}>{m.title}</div>
            <div className="match-badges">
              <span className={`chip ${m.tempo.score > 0.6 ? 'good' : m.tempo.score > 0.25 ? '' : 'bad'}`}>
                {Math.round(m.tempo.from ?? 0)} → {Math.round(m.tempo.to ?? 0)} BPM
                {Math.abs(m.tempo.stretch - 1) > 0.002 ? ` (${signed(Math.round((m.tempo.stretch - 1) * 1000) / 10)}%)` : ''}
              </span>
              <span className={`chip ${m.key.distance <= 1 ? 'good' : m.key.distance === 2 ? '' : 'bad'}`}>
                {m.key.camelot[0]} → {m.key.camelot[1]}
                {m.key.shift ? ` (${signed(m.key.shift)} st)` : ''}
              </span>
              {m.shared_themes.slice(0, 4).map((t) => (
                <span key={t} className="chip mood">{t}</span>
              ))}
            </div>
          </div>
          <div className="match-actions">
            <button disabled={!hasVocals} title={hasVocals ? '' : 'This song has no vocals'} onClick={() => onMashup(song.id, m.id)}>
              This vocal over it
            </button>
            <button disabled={!m.has_vocals} title={m.has_vocals ? '' : 'That song has no vocals'} onClick={() => onMashup(m.id, song.id)}>
              Its vocal over this
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
