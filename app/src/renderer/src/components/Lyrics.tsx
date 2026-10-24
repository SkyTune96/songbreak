import { useEffect, useMemo, useRef } from 'react'
import type { Line, Section } from '../types'
import { fmtTime, letterColor } from '../util'

interface Props {
  lines: Line[]
  sections: Section[]
  time: number
  playing: boolean
  onSeek: (t: number) => void
}

/** Karaoke view: the current line and word light up, and the list follows playback. */
export function Lyrics({ lines, sections, time, playing, onSeek }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const lastUserScroll = useRef(0)

  const grouped = useMemo(() => {
    const out: { section: Section | null; lines: { line: Line; index: number }[] }[] = []
    lines.forEach((line, index) => {
      const mid = (line.start + line.end) / 2
      const section = sections.find((s) => s.start <= mid && mid < s.end) ?? null
      const last = out[out.length - 1]
      if (last && last.section === section) last.lines.push({ line, index })
      else out.push({ section, lines: [{ line, index }] })
    })
    return out
  }, [lines, sections])

  let current = -1
  for (let i = 0; i < lines.length; i++) if (lines[i].start <= time + 0.05) current = i

  useEffect(() => {
    // Follow playback, but not for a few seconds after the user scrolls by hand.
    if (!playing || current < 0 || Date.now() - lastUserScroll.current < 4000) return
    box.current?.querySelector(`[data-line="${current}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [current, playing])

  if (!lines.length)
    return <div className="empty-panel">No lyrics were found. The track may be instrumental, or the vocals too buried to transcribe.</div>

  return (
    <div className="lyrics" ref={box} onWheel={() => (lastUserScroll.current = Date.now())}>
      {grouped.map((g, gi) => (
        <div key={gi} className="lyric-group">
          {g.section && (
            <div className="lyric-section" style={{ color: letterColor(g.section.letter) }}>
              {g.section.label}
            </div>
          )}
          {g.lines.map(({ line, index }) => {
            const state = index === current ? 'now' : index < current ? 'past' : ''
            return (
              <div key={index} data-line={index} className={`lyric-line ${state}`} onClick={() => onSeek(line.start)}>
                <span className="lyric-time">{fmtTime(line.start)}</span>
                <span className="lyric-text">
                  {index === current
                    ? line.words.map((w, wi) => (
                        <span key={wi} className={w.start <= time ? 'sung' : ''}>
                          {w.word}{' '}
                        </span>
                      ))
                    : line.text}
                </span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
