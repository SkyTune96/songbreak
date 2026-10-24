import type { Song } from '../types'
import { fmtTime, letterColor, stemColor } from '../util'

export function Sections({ song, time, onSeek }: { song: Song; time: number; onSeek: (t: number) => void }) {
  const st = song.structure
  if (!st) return <div className="empty-panel">Structure analysis was skipped for this song.</div>
  return (
    <div className="sections">
      <table>
        <thead>
          <tr>
            <th />
            <th>Section</th>
            <th>Time</th>
            <th>Length</th>
            <th>Energy</th>
            <th>Playing</th>
          </tr>
        </thead>
        <tbody>
          {st.sections.map((s, i) => {
            const now = time >= s.start && time < s.end
            return (
              <tr key={i} className={now ? 'now' : ''} onClick={() => onSeek(s.start)}>
                <td>
                  <span className="letter" style={{ background: letterColor(s.letter) }}>
                    {s.letter}
                  </span>
                </td>
                <td>
                  {s.label}
                  {s.label_auto && s.label_auto !== s.label && <span className="muted small"> (auto: {s.label_auto})</span>}
                </td>
                <td className="mono">
                  {fmtTime(s.start)}–{fmtTime(s.end)}
                </td>
                <td className="mono">{Math.round(s.end - s.start)}s</td>
                <td>
                  <span className="energy">
                    <span style={{ width: `${Math.max(4, s.energy * 100)}%`, background: letterColor(s.letter) }} />
                  </span>
                </td>
                <td>
                  {s.stems_active.map((n) => (
                    <span key={n} className="mini-dot" title={n} style={{ background: stemColor(n) }} />
                  ))}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="muted small">
        Same letter = same musical material. Boundaries come from changes in the backing track; names come from
        repetition and lyrics{song.story?.llm ? ', checked by the LLM' : ''}.
      </div>
    </div>
  )
}
