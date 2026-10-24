import type { ChordSegment } from '../api'

export function chordAt(chords: ChordSegment[], t: number): string | null {
  const c = chords.find((s) => s.start <= t && t < s.end)
  return c && c.chord !== 'N' ? c.chord : null
}

/** Chord names along the timeline, aligned with the waveform below; click to jump. */
export function ChordLane({ chords, duration, time, onSeek }: { chords: ChordSegment[]; duration: number; time: number; onSeek: (t: number) => void }) {
  if (!duration || !chords.length) return null
  return (
    <div className="chord-lane">
      {chords
        .filter((c) => c.chord !== 'N')
        .map((c, i) => {
          const now = time >= c.start && time < c.end
          return (
            <button
              key={i}
              className={`chord ${now ? 'now' : ''} ${c.chord.endsWith('m') ? 'minor' : ''}`}
              style={{ left: `${(c.start / duration) * 100}%`, width: `${((c.end - c.start) / duration) * 100}%` }}
              onClick={() => onSeek(c.start)}
              title={`${c.chord} at ${Math.floor(c.start / 60)}:${String(Math.floor(c.start % 60)).padStart(2, '0')}`}
            >
              <span>{c.chord}</span>
            </button>
          )
        })}
    </div>
  )
}
