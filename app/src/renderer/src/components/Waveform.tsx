import { useEffect, useRef, useState } from 'react'
import type { Section } from '../types'
import { fmtTime, letterColor } from '../util'

interface Props {
  peaks: number[]
  duration: number
  time: number
  color: string
  height: number
  sections?: Section[]
  dim?: boolean
  onSeek?: (t: number) => void
}

/** Canvas waveform. Redraws on resize and as `time` moves; peaks are pre-computed by the backend. */
export function Waveform({ peaks, duration, time, color, height, sections, dim, onSeek }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const c = canvas.current
    if (!c || !width) return
    const dpr = window.devicePixelRatio || 1
    c.width = width * dpr
    c.height = height * dpr
    const g = c.getContext('2d')!
    g.scale(dpr, dpr)
    g.clearRect(0, 0, width, height)
    const x = (t: number): number => (duration ? (t / duration) * width : 0)
    const labelBand = sections ? 18 : 0

    if (sections) {
      for (const s of sections) {
        const c0 = letterColor(s.letter)
        g.fillStyle = c0 + '1f'
        g.fillRect(x(s.start), 0, x(s.end) - x(s.start), height)
        g.fillStyle = c0
        g.fillRect(x(s.start), 0, x(s.end) - x(s.start) - 1, 3)
        g.fillStyle = 'rgba(255,255,255,0.08)'
        g.fillRect(x(s.start), 0, 1, height)
        g.font = '600 11px "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif'
        g.fillStyle = c0
        const w = x(s.end) - x(s.start)
        if (w > 36) {
          g.save()
          g.beginPath()
          g.rect(x(s.start), 0, w - 4, labelBand)
          g.clip()
          g.fillText(s.label, x(s.start) + 6, 14)
          g.restore()
        }
      }
    }

    const mid = labelBand + (height - labelBand) / 2
    // The overview fills its lane; stem rows keep true levels so quiet stems look quiet.
    const peak = sections ? Math.max(0.05, ...peaks) : 1
    const amp = ((height - labelBand) / 2 - 2) / peak
    const n = peaks.length
    const bar = Math.max(1, width / Math.max(n, 1))
    const step = n > width ? n / width : 1
    const playedX = x(time)
    const cols = Math.min(n, width)
    for (let i = 0; i < cols; i++) {
      const a = Math.floor(i * step)
      const b = Math.max(a + 1, Math.floor((i + 1) * step))
      let p = 0
      for (let j = a; j < b && j < n; j++) p = Math.max(p, peaks[j])
      const px = n > width ? i : i * bar
      const h = Math.max(1, p * amp)
      g.fillStyle = px < playedX ? color : color + (dim ? '40' : '66')
      g.fillRect(px, mid - h, Math.max(1, bar - (bar > 2 ? 1 : 0)), h * 2)
    }

    if (onSeek) {
      g.fillStyle = '#ffffff'
      g.fillRect(Math.round(playedX), 0, 2, height)
    }
  }, [peaks, duration, time, color, height, sections, width, dim, onSeek])

  const tAt = (e: React.MouseEvent): number => {
    const r = wrap.current!.getBoundingClientRect()
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * duration
  }

  return (
    <div
      ref={wrap}
      className={`waveform ${onSeek ? 'seekable' : ''}`}
      style={{ height }}
      onClick={onSeek ? (e) => onSeek(tAt(e)) : undefined}
      onMouseMove={onSeek ? (e) => setHover(tAt(e)) : undefined}
      onMouseLeave={() => setHover(null)}
    >
      <canvas ref={canvas} style={{ width: '100%', height }} />
      {hover != null && duration > 0 && (
        <div className="wave-hover" style={{ left: `${(hover / duration) * 100}%` }}>
          <span>{fmtTime(hover)}</span>
        </div>
      )}
    </div>
  )
}
