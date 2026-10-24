import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChordSegment } from '../api'
import type { Line, Section } from '../types'
import { fmtTime, letterColor } from '../util'
import { chordAt } from './Chords'

interface Props {
  lines: Line[]
  sections: Section[]
  chords: ChordSegment[]
  time: number
  playing: boolean
  onSeek: (t: number) => void
}

/** Chord shown above a word when it differs from the chord over the previous word (lead-sheet style). */
function chordMarks(line: Line, chords: ChordSegment[]): (string | null)[] {
  let prev: string | null = null
  return line.words.map((w, i) => {
    const c = chordAt(chords, w.start + 0.05)
    const show = c && (i === 0 || c !== prev) ? c : null
    prev = c
    return show
  })
}

/** Karaoke view: the current line and word light up, and the list follows playback. */
export function Lyrics({ lines, sections, chords, time, playing, onSeek }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const [showChords, setShowChords] = useState(true)
  const withChords = showChords && chords.length > 0
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
      {chords.length > 0 && (
        <label className="lyrics-toggle muted small">
          <input type="checkbox" checked={showChords} onChange={(e) => setShowChords(e.target.checked)} /> Show chords
        </label>
      )}
      {grouped.map((g, gi) => (
        <div key={gi} className="lyric-group">
          {g.section && (
            <div className="lyric-section" style={{ color: letterColor(g.section.letter) }}>
              {g.section.label}
            </div>
          )}
          {g.lines.map(({ line, index }) => {
            const state = index === current ? 'now' : index < current ? 'past' : ''
            const marks = withChords ? chordMarks(line, chords) : null
            return (
              <div key={index} data-line={index} className={`lyric-line ${state} ${withChords ? 'chorded' : ''}`} onClick={() => onSeek(line.start)}>
                <span className="lyric-time">{fmtTime(line.start)}</span>
                <span className="lyric-text">
                  {index === current || marks
                    ? line.words.map((w, wi) => (
                        // The space sits between word boxes: inside an inline-block a trailing space is dropped.
                        <span key={wi}>
                          <span className={`w ${index === current && w.start <= time ? 'sung' : ''}`}>
                            {marks && <span className="c">{marks[wi] ?? ''}</span>}
                            <span className="t">{w.word}</span>
                          </span>{' '}
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
