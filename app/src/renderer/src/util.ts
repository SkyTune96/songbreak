export function fmtTime(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return '–:––'
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

export function pct(v: number): string {
  return `${Math.round(v * 100)}%`
}

export const STEM_COLORS: Record<string, string> = {
  vocals: '#ff5c8a',
  drums: '#ffa640',
  bass: '#9d7bff',
  guitar: '#4fd18b',
  piano: '#ffd84d',
  other: '#3cc8e8',
  mix: '#c9ccd6'
}

export const STEM_ORDER = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other']

export function stemColor(name: string): string {
  return STEM_COLORS[name] ?? '#8a90a0'
}

const SECTION_PALETTE = ['#5b8cff', '#ff6b9a', '#ffb547', '#3fd0a6', '#b98bff', '#4fc3f7', '#f78c6b', '#a5d65c']

export function letterColor(letter: string): string {
  return SECTION_PALETTE[(letter.charCodeAt(0) - 65) % SECTION_PALETTE.length]
}

export const BEAT_COLORS: Record<string, string> = {
  exposition: '#5b8cff',
  'rising action': '#ffb547',
  climax: '#ff5c6c',
  'falling action': '#b98bff',
  resolution: '#3fd0a6',
  refrain: '#8a90a0'
}

export function beatColor(beat?: string): string {
  return BEAT_COLORS[(beat ?? '').toLowerCase()] ?? '#8a90a0'
}

export function sumPeaks(peaks: Record<string, number[]>): number[] {
  if (peaks.mix) return peaks.mix
  const stems = Object.values(peaks)
  if (!stems.length) return []
  return stems[0].map((_, i) => Math.min(1, stems.reduce((a, s) => a + (s[i] ?? 0), 0)))
}

export function cleanLabel(l: string): string {
  return l.replace(/^(\w)/, (c) => c.toUpperCase())
}
