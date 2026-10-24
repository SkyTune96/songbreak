import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Playlist } from '../api'
import { beatColor, fmtTime } from '../util'

interface Props {
  playlist: Playlist
  onOpenSong: (id: string) => void
  onRename: (name: string) => void
  onDelete: () => void
}

const XF = 6 // crossfade seconds

/** Target arc (lines) with each song's own reading (dots) at its position. */
function ArcChart({ p, current }: { p: Playlist; current: number }) {
  const W = 900
  const H = 200
  const pad = { l: 34, r: 14, t: 14, b: 30 }
  const n = p.songs.length
  const x = (i: number): number => pad.l + (n > 1 ? i / (n - 1) : 0.5) * (W - pad.l - pad.r)
  const yI = (v: number): number => pad.t + (1 - v) * (H - pad.t - pad.b)
  const yV = (v: number): number => yI((v + 1) / 2)
  const line = (f: (i: number) => number): string => p.songs.map((_, i) => `${x(i).toFixed(1)},${f(i).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="arc-chart" preserveAspectRatio="none">
      {[0, 0.5, 1].map((v) => (
        <line key={v} x1={pad.l} x2={W - pad.r} y1={yI(v)} y2={yI(v)} className="grid" />
      ))}
      <text x={4} y={yI(1) + 4} className="axis">high</text>
      <text x={4} y={yI(0) + 4} className="axis">low</text>
      <polyline points={line((i) => yV(p.songs[i].target.valence))} className="target-valence" />
      <polyline points={line((i) => yI(p.songs[i].target.intensity))} className="target-intensity" />
      {p.songs.map((s, i) => (
        <g key={s.id}>
          <line x1={x(i)} x2={x(i)} y1={yI(s.target.intensity)} y2={yI(s.affect.intensity)} className="miss" />
          <circle cx={x(i)} cy={yV(s.affect.valence)} r={4} className="dot-valence" />
          <circle cx={x(i)} cy={yI(s.affect.intensity)} r={i === current ? 8 : 6} className={`dot-intensity ${i === current ? 'now' : ''}`}>
            <title>{`${s.title}\nintensity ${s.affect.intensity} (target ${s.target.intensity})\nmood ${s.affect.valence} (target ${s.target.valence})\n${s.affect.why}`}</title>
          </circle>
          <text x={x(i)} y={H - 10} className="beat-label" textAnchor="middle" fill={beatColor(s.beat)}>
            {i + 1}
          </text>
        </g>
      ))}
    </svg>
  )
}

export function PlaylistView({ playlist: p, onOpenSong, onRename, onDelete }: Props) {
  const decks = useRef<[HTMLAudioElement, HTMLAudioElement]>([new Audio(), new Audio()])
  const active = useRef(0) // which deck is the current track
  const fading = useRef(false)
  const [current, setCurrent] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [editing, setEditing] = useState(false)
  const playable = p.songs.map((s) => !!s.source_file)

  const url = (i: number): string => api.fileUrl(p.songs[i].id, p.songs[i].source_file ?? '')
  const nextPlayable = useCallback(
    (from: number): number => {
      for (let i = from; i < p.songs.length; i++) if (playable[i]) return i
      return -1
    },
    [p.songs.length, playable]
  )

  const start = useCallback(
    (i: number): void => {
      const [a, b] = decks.current
      a.pause()
      b.pause()
      fading.current = false
      active.current = 0
      a.src = url(i)
      a.volume = 1
      a.play()
      setCurrent(i)
      setPlaying(true)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [p.id]
  )

  // Crossfade engine: near the end of the current track, start the next on the other deck and swap volumes.
  useEffect(() => {
    let raf = 0
    const tick = (): void => {
      const cur = decks.current[active.current]
      const other = decks.current[1 - active.current]
      setTime(cur.currentTime || 0)
      setDuration(cur.duration || 0)
      if (playing && current >= 0 && cur.duration && !fading.current && cur.duration - cur.currentTime <= XF) {
        const n = nextPlayable(current + 1)
        if (n >= 0) {
          fading.current = true
          other.src = url(n)
          other.volume = 0
          other.play()
          active.current = 1 - active.current // the clock and controls follow the incoming track from here
          const t0 = performance.now()
          const step = (): void => {
            const f = Math.min(1, (performance.now() - t0) / (XF * 1000))
            cur.volume = Math.cos((f * Math.PI) / 2) // equal-power fade
            other.volume = Math.sin((f * Math.PI) / 2)
            if (f < 1) requestAnimationFrame(step)
            else {
              cur.pause()
              fading.current = false
            }
          }
          requestAnimationFrame(step)
          setCurrent(n)
        }
      }
      if (playing && cur.ended && !fading.current) setPlaying(false)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, current, nextPlayable])

  useEffect(() => {
    const d = decks.current
    return () => d.forEach((a) => (a.pause(), (a.src = '')))
  }, [p.id])

  const toggle = (): void => {
    if (current < 0) {
      const first = nextPlayable(0)
      if (first >= 0) start(first)
      return
    }
    const cur = decks.current[active.current]
    if (playing) {
      decks.current.forEach((a) => a.pause())
      setPlaying(false)
    } else {
      cur.play()
      setPlaying(true)
    }
  }

  const seek = (t: number): void => {
    if (fading.current) return // mid-crossfade both decks are playing; let it finish
    decks.current[active.current].currentTime = Math.max(0, Math.min(t, duration - 0.5))
  }
  const prevPlayable = (from: number): number => {
    for (let i = from; i >= 0; i--) if (playable[i]) return i
    return -1
  }

  const totalSongs = p.songs.length
  return (
    <div className="song-view playlist-view">
      <header className="song-header">
        <div>
          <div className="field-label">Story playlist · {p.arc_name}</div>
          {editing ? (
            <input
              className="rename big"
              autoFocus
              defaultValue={p.name}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (onRename(e.currentTarget.value), setEditing(false))
                if (e.key === 'Escape') setEditing(false)
              }}
              onBlur={(e) => (onRename(e.currentTarget.value), setEditing(false))}
            />
          ) : (
            <h1 onDoubleClick={() => setEditing(true)} title="Double-click to rename">{p.name}</h1>
          )}
          <div className="meta">
            <span>{totalSongs} songs</span>
            {p.written_by && <span>story by {p.written_by}</span>}
          </div>
        </div>
        <div className="header-actions">
          <button onClick={() => setEditing(true)}>Rename</button>
          <button className="danger-btn" onClick={onDelete}>Delete</button>
        </div>
      </header>

      <blockquote className="summary">{p.synopsis}</blockquote>

      <div className="transport">
        <button className="play" onClick={toggle} title="Play the playlist with crossfades">
          {playing ? (
            <svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
          ) : (
            <svg viewBox="0 0 24 24"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" /></svg>
          )}
        </button>
        <div className="now-section">
          {current >= 0 ? (
            <>
              <span className="muted">{current + 1}/{totalSongs}</span> {p.songs[current].title}
              <span className="muted"> · {fmtTime(time)}</span>
            </>
          ) : (
            <span className="muted">Plays start to finish with {XF}s crossfades</span>
          )}
        </div>
      </div>

      {current >= 0 && (
        <div className="seekbar">
          <button className="link" title="Previous track" disabled={prevPlayable(current - 1) < 0} onClick={() => start(prevPlayable(current - 1))}>⏮</button>
          <span className="mono">{fmtTime(time)}</span>
          <input
            type="range"
            min={0}
            max={duration || 1}
            step={0.1}
            value={Math.min(time, duration || 1)}
            onChange={(e) => seek(+e.target.value)}
            title="Drag to jump within the current track"
          />
          <span className="mono">{fmtTime(duration)}</span>
          <button className="link" title="Next track" disabled={nextPlayable(current + 1) < 0} onClick={() => start(nextPlayable(current + 1))}>⏭</button>
          <button
            title={`Jump to ${XF + 4}s before the end to hear the crossfade into the next track`}
            disabled={nextPlayable(current + 1) < 0 || !duration}
            onClick={() => seek(duration - XF - 4)}
          >
            Skip to fade
          </button>
        </div>
      )}

      <div className="main-wave">
        <ArcChart p={p} current={current} />
        <div className="legend muted small">
          <span className="lg-intensity" /> target intensity · ● each song's intensity
          <span className="lg-valence" /> target mood · ● each song's mood (dark → hopeful)
        </div>
      </div>

      <div className="tracklist">
        {p.songs.map((s, i) => (
          <div key={s.id}>
            <div className={`track ${i === current ? 'now' : ''} ${playable[i] ? '' : 'missing'}`} onClick={() => playable[i] && start(i)}>
              <span className="track-n">{i + 1}</span>
              <div className="track-main">
                <div className="track-head">
                  <span className="track-title">{s.title}</span>
                  <span className="chip" style={{ color: beatColor(s.beat), borderColor: beatColor(s.beat) + '66' }}>{s.beat}</span>
                </div>
                <div className="track-chapter">{s.chapter}</div>
                <div className="muted small" title={s.affect.source === 'llm' ? 'Mood read by the LLM' : 'Estimated from audio'}>
                  intensity {Math.round(s.affect.intensity * 100)}% · mood {s.affect.valence > 0.15 ? 'hopeful' : s.affect.valence < -0.15 ? 'dark' : 'bittersweet'}
                  {s.affect.why ? ` · ${s.affect.why}` : ''}
                </div>
              </div>
              <button className="link" onClick={(e) => (e.stopPropagation(), onOpenSong(s.id))} title="Open song">Open ›</button>
            </div>
            {s.next && (
              <div className={`transition ${s.next.score > 0.6 ? 'good' : s.next.score > 0.35 ? '' : 'rough'}`}>
                ↓ {Math.round(s.next.tempo[0])}→{Math.round(s.next.tempo[1])} BPM · {s.next.camelot[0]}→{s.next.camelot[1]} ·{' '}
                {s.next.score > 0.6 ? 'smooth transition' : s.next.score > 0.35 ? 'okay transition' : 'big change in tempo or key'}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
