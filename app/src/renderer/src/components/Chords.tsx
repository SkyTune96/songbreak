import { useEffect, useRef, useState } from 'react'
import type { ChordSegment } from '../api'

const MIN_LABEL_PX = 26 // narrower blocks stay unlabelled (hover shows the chord) so dense songs stay readable

export function chordAt(chords: ChordSegment[], t: number): string | null {
  const c = chords.find((s) => s.start <= t && t < s.end)
  return c && c.chord !== 'N' ? c.chord : null
}

/** Chord names along the timeline, aligned with the waveform below; click to jump. */
export function ChordLane({ chords, duration, time, onSeek }: { chords: ChordSegment[]; duration: number; time: number; onSeek: (t: number) => void }) {
  const lane = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = lane.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [chords.length > 0])
  if (!duration || !chords.length) return null
  return (
    <div className="chord-lane" ref={lane}>
      {chords
        .filter((c) => c.chord !== 'N')
        .map((c, i) => {
          const now = time >= c.start && time < c.end
          const px = ((c.end - c.start) / duration) * width
          return (
            <button
              key={i}
              className={`chord ${now ? 'now' : ''} ${c.chord.endsWith('m') ? 'minor' : ''} ${px < MIN_LABEL_PX ? 'tiny' : ''}`}
              style={{ left: `${(c.start / duration) * 100}%`, width: `${((c.end - c.start) / duration) * 100}%` }}
              onClick={() => onSeek(c.start)}
              title={`${c.chord} at ${Math.floor(c.start / 60)}:${String(Math.floor(c.start % 60)).padStart(2, '0')}`}
            >
              {px >= MIN_LABEL_PX && <span>{c.chord}</span>}
            </button>
          )
        })}
    </div>
  )
}
