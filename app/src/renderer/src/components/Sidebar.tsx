import { useState } from 'react'
import type { Job, SongSummary, SystemInfo } from '../types'
import { fmtTime } from '../util'
import { Logo } from './Logo'

interface Props {
  songs: SongSummary[]
  jobs: Job[]
  selected: string | null
  view: string
  system: SystemInfo | null
  onSelect: (id: string) => void
  onAdd: () => void
  onCancel: (job: Job) => void
  onDismiss: (job: Job) => void
  onSettings: () => void
  onHome: () => void
}

const STAGE_NAMES: Record<string, string> = {
  load: 'Decoding',
  stems: 'Separating stems',
  lyrics: 'Transcribing lyrics',
  structure: 'Mapping structure',
  instruments: 'Naming instruments',
  story: 'Writing the story',
  peaks: 'Finishing'
}

export function Sidebar(p: Props) {
  const [q, setQ] = useState('')
  const shown = p.songs.filter((s) => s.title.toLowerCase().includes(q.toLowerCase()))
  const visibleJobs = p.jobs.filter((j) => j.status !== 'done' && j.status !== 'cancelled')
  const profile = p.system?.profile

  return (
    <aside className="sidebar">
      <div className="brand" onClick={p.onHome}>
        <Logo size={26} />
        <span>Songbreak</span>
      </div>
      <button className="primary add" onClick={p.onAdd}>
        <span className="plus">+</span> Add songs
      </button>

      {visibleJobs.length > 0 && (
        <div className="jobs">
          <div className="side-label">Queue</div>
          {visibleJobs.map((j) => (
            <div key={j.id} className={`job ${j.status}`}>
              <div className="job-head">
                <span className="job-title" title={j.source}>{j.title}</span>
                {(j.status === 'queued' || j.status === 'running') && (
                  <button className="link" onClick={() => p.onCancel(j)} title="Cancel">✕</button>
                )}
                {j.status === 'error' && (
                  <button className="link" onClick={() => p.onDismiss(j)} title="Dismiss">✕</button>
                )}
              </div>
              {j.status === 'running' && (
                <>
                  <div className="progress"><span style={{ width: `${j.progress * 100}%` }} /></div>
                  <div className="job-msg">
                    {STAGE_NAMES[j.stage ?? ''] ?? j.message} · {Math.round(j.progress * 100)}%
                  </div>
                </>
              )}
              {j.status === 'queued' && <div className="job-msg">Waiting…</div>}
              {j.status === 'error' && <div className="job-msg err" title={j.error ?? ''}>{j.error}</div>}
            </div>
          ))}
        </div>
      )}

      <div className="side-label">Library</div>
      {p.songs.length > 4 && (
        <input className="search" placeholder="Search songs" value={q} onChange={(e) => setQ(e.target.value)} />
      )}
      <div className="song-list">
        {shown.map((s) => (
          <button
            key={s.id}
            className={`song-item ${p.view === 'song' && p.selected === s.id ? 'on' : ''}`}
            onClick={() => p.onSelect(s.id)}
          >
            <span className="song-item-title">
              {s.kind === 'mashup' && <span className="tag">mashup</span>}
              {s.title}
            </span>
            <span className="song-item-meta">
              {fmtTime(s.duration)}
              {s.key ? ` · ${s.key}` : ''}
              {s.tempo ? ` · ${Math.round(s.tempo)} BPM` : ''}
            </span>
          </button>
        ))}
        {!p.songs.length && <div className="muted small pad">Songs you analyse appear here.</div>}
      </div>

      <div className="side-footer">
        <div className="hw" title={profile ? `${profile.gpu}\n${profile.vram_gb} GB VRAM · ${profile.ram_gb} GB RAM` : ''}>
          <span className={`led ${p.system?.ready ? 'ok' : ''}`} />
          {profile ? (
            <span>{profile.gpu.replace('NVIDIA GeForce ', '')} · {profile.name}</span>
          ) : (
            <span>Warming up…</span>
          )}
        </div>
        <button className={`icon-btn ${p.view === 'settings' ? 'on' : ''}`} onClick={p.onSettings} title="Settings">
          <svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm7.4-2.5.04-1-.04-1 2.1-1.65-2-3.46-2.5 1a7.6 7.6 0 0 0-1.73-1L14.9 3h-4l-.37 2.6a7.6 7.6 0 0 0-1.73 1l-2.5-1-2 3.46L6.4 10.7 6.36 12l.04 1.3-2.1 1.64 2 3.46 2.5-1c.53.4 1.11.74 1.73 1L10.9 21h4l.37-2.6a7.6 7.6 0 0 0 1.73-1l2.5 1 2-3.46-2.1-1.64z" /></svg>
        </button>
      </div>
    </aside>
  )
}
