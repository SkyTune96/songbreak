import { useEffect, useMemo, useState } from 'react'
import { api, type Arc, type FolderData } from '../api'
import type { SongSummary } from '../types'

interface Props {
  songs: SongSummary[]
  folders: FolderData
  initialFolder?: string | null
  onCreate: (songIds: string[], arc: string) => void
  onClose: () => void
}

/** Tiny sparkline of an arc: intensity solid, valence dashed. */
export function ArcSpark({ arc, w = 120, h = 40 }: { arc: Arc; w?: number; h?: number }) {
  const pts = (key: 'intensity' | 'valence'): string =>
    arc.curve
      .map((p) => {
        const v = key === 'valence' ? (p.valence + 1) / 2 : p.intensity
        return `${(p.x * (w - 4) + 2).toFixed(1)},${(h - 2 - v * (h - 4)).toFixed(1)}`
      })
      .join(' ')
  return (
    <svg width={w} height={h} className="arc-spark">
      <polyline points={pts('valence')} className="spark-valence" />
      <polyline points={pts('intensity')} className="spark-intensity" />
    </svg>
  )
}

export function PlaylistDialog({ songs, folders, initialFolder, onCreate, onClose }: Props) {
  const [arcs, setArcs] = useState<Arc[]>([])
  const [arc, setArc] = useState('rise_resolve')
  const [source, setSource] = useState<string>(initialFolder ?? '__all__')
  const eligible = useMemo(() => songs.filter((s) => s.kind !== 'mashup'), [songs])
  const inSource = useMemo(
    () => (source === '__all__' ? eligible : eligible.filter((s) => folders.assignments[s.id] === source)),
    [source, eligible, folders]
  )
  const [picked, setPicked] = useState<Set<string>>(new Set())

  useEffect(() => {
    api.arcs().then(setArcs).catch(() => undefined)
  }, [])
  useEffect(() => setPicked(new Set(inSource.map((s) => s.id))), [inSource])
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const chosen = inSource.filter((s) => picked.has(s.id))
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">New story playlist</h2>
        <p className="muted">
          Songbreak reads each song's mood from its story analysis, then orders them so the playlist follows the arc you pick,
          preferring smooth tempo and key transitions. Your local LLM writes the playlist's story.
        </p>

        <div className="field-label">Arc</div>
        <div className="arc-grid">
          {arcs.map((a) => (
            <button key={a.id} className={`arc-card ${arc === a.id ? 'on' : ''}`} onClick={() => setArc(a.id)}>
              <ArcSpark arc={a} />
              <strong>{a.name}</strong>
              <span>{a.description}</span>
            </button>
          ))}
        </div>
        <div className="legend muted small">
          <span className="lg-intensity" /> intensity <span className="lg-valence" /> mood (dark → hopeful)
        </div>

        <div className="field-label">Songs</div>
        <div className="row-inline">
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="__all__">Whole library</option>
            {folders.folders.map((f) => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))}
          </select>
          <span className="muted small">{chosen.length} of {inSource.length} selected · mashups aren't included</span>
        </div>
        <div className="pick-list">
          {inSource.map((s) => (
            <label key={s.id} className="pick">
              <input
                type="checkbox"
                checked={picked.has(s.id)}
                onChange={(e) =>
                  setPicked((p) => {
                    const n = new Set(p)
                    if (e.target.checked) n.add(s.id)
                    else n.delete(s.id)
                    return n
                  })
                }
              />
              <span>{s.title}</span>
              {!s.has_story && <span className="muted small"> (no story yet: mood is estimated from audio)</span>}
            </label>
          ))}
          {!inSource.length && <div className="muted small">No songs here.</div>}
        </div>

        <div className="btns modal-actions">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={chosen.length < 2} onClick={() => onCreate(chosen.map((s) => s.id), arc)}>
            {chosen.length < 2 ? 'Pick at least 2 songs' : `Create from ${chosen.length} songs`}
          </button>
        </div>
      </div>
    </div>
  )
}
