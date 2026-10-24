import type { Scored, Song } from '../types'
import { fmtTime, letterColor, stemColor } from '../util'

function Bars({ title, items }: { title: string; items: Scored[] }) {
  if (!items.length) return null
  const max = Math.max(...items.map(([, v]) => v))
  return (
    <div className="bars">
      <h3>{title}</h3>
      {items.slice(0, 10).map(([label, v]) => (
        <div key={label} className="bar-row">
          <span className="bar-label">{label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(v / max) * 100}%` }} />
          </span>
          <span className="bar-value">{Math.round(v * 100)}%</span>
        </div>
      ))}
    </div>
  )
}

export function Instruments({ song, onSeek }: { song: Song; onSeek: (t: number) => void }) {
  const tags = song.instruments
  const sections = song.structure?.sections ?? []
  if (!tags) return <div className="empty-panel">Instrument tagging was skipped for this song.</div>
  return (
    <div className="instruments">
      <div className="bars-grid">
        <Bars title="Instruments heard" items={tags.song.instruments} />
        <div>
          <Bars title="Vocal style" items={tags.song.vocal_style} />
          <Bars title="Genre" items={tags.song.genre} />
          <Bars title="Mood" items={tags.song.mood} />
        </div>
      </div>
      <h3>By section</h3>
      <div className="section-cards">
        {sections.map((s, i) => {
          const t = tags.sections[i]
          return (
            <div key={i} className="section-card" onClick={() => onSeek(s.start)} style={{ borderLeftColor: letterColor(s.letter) }}>
              <div className="section-card-head">
                <span>{s.label}</span>
                <span className="muted">
                  {fmtTime(s.start)}–{fmtTime(s.end)}
                </span>
              </div>
              <div className="stem-dots">
                {s.stems_active.map((st) => (
                  <span key={st} className="stem-pill" style={{ borderColor: stemColor(st), color: stemColor(st) }}>
                    {st}
                  </span>
                ))}
              </div>
              <div className="chips">
                {[...(t?.instrument ?? []), ...(t?.vocal ?? [])].slice(0, 6).map(([lb]) => (
                  <span key={lb} className="chip">
                    {lb}
                  </span>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <div className="muted small">Tagged by an AudioSet classifier (AST); percentages are its confidence, not loudness.</div>
    </div>
  )
}
