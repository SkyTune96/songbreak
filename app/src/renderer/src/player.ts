/**
 * Plays every stem in sample-accurate sync with Web Audio.
 *
 * All tracks (stems + the original mix) start on the same AudioContext clock, so mute,
 * solo, volume and the stems/original A-B switch are just gain changes: nothing restarts
 * and nothing drifts.
 */

export interface TrackState {
  volume: number
  muted: boolean
  solo: boolean
}

interface Track extends TrackState {
  buffer: AudioBuffer
  gain: GainNode
  source: AudioBufferSourceNode | null
}

export const ORIGINAL = '__original__'
const RAMP = 0.015 // s; avoids clicks on gain changes

export class StemPlayer {
  private ctx = new AudioContext()
  private master = this.ctx.createGain()
  private tracks = new Map<string, Track>()
  private startedAt = 0 // ctx time at which offset 0 would have played
  private offset = 0
  private abort: AbortController | null = null
  playing = false
  mode: 'stems' | 'original' = 'stems'
  duration = 0
  onEnded: (() => void) | null = null

  constructor() {
    this.master.connect(this.ctx.destination)
  }

  /** Fetch and decode tracks. `onProgress` gets 0..1 across all files. */
  async load(urls: Record<string, string>, onProgress?: (f: number) => void): Promise<void> {
    this.stop()
    this.abort?.abort()
    const abort = (this.abort = new AbortController())
    this.tracks.forEach((t) => t.gain.disconnect())
    this.tracks.clear()
    const names = Object.keys(urls)
    let done = 0
    const decoded = await Promise.all(
      names.map(async (name) => {
        const r = await fetch(urls[name], { signal: abort.signal })
        const buf = await this.ctx.decodeAudioData(await r.arrayBuffer())
        onProgress?.(++done / names.length)
        return [name, buf] as const
      })
    )
    if (abort.signal.aborted) return
    for (const [name, buffer] of decoded) {
      const gain = this.ctx.createGain()
      gain.connect(this.master)
      this.tracks.set(name, { buffer, gain, source: null, volume: 1, muted: false, solo: false })
    }
    this.duration = Math.max(0, ...decoded.map(([, b]) => b.duration))
    this.offset = 0
    this.applyGains(true)
  }

  get currentTime(): number {
    if (!this.playing) return this.offset
    return Math.min(this.ctx.currentTime - this.startedAt, this.duration)
  }

  has(name: string): boolean {
    return this.tracks.has(name)
  }

  state(name: string): TrackState | undefined {
    const t = this.tracks.get(name)
    return t && { volume: t.volume, muted: t.muted, solo: t.solo }
  }

  play(): void {
    if (this.playing || !this.tracks.size) return
    if (this.ctx.state === 'suspended') this.ctx.resume()
    if (this.offset >= this.duration - 0.05) this.offset = 0
    const when = this.ctx.currentTime + 0.05 // schedule slightly ahead so every source starts together
    this.startedAt = when - this.offset
    for (const t of this.tracks.values()) {
      const src = this.ctx.createBufferSource()
      src.buffer = t.buffer
      src.connect(t.gain)
      src.start(when, this.offset)
      t.source = src
    }
    this.playing = true
  }

  pause(): void {
    if (!this.playing) return
    this.offset = this.currentTime
    this.stopSources()
    this.playing = false
  }

  toggle(): void {
    this.playing ? this.pause() : this.play()
  }

  seek(t: number): void {
    const was = this.playing
    if (was) this.stopSources()
    this.playing = false
    this.offset = Math.max(0, Math.min(t, this.duration))
    if (was) this.play()
  }

  /** Call every animation frame; fires onEnded once playback runs off the end. */
  tick(): void {
    if (this.playing && this.currentTime >= this.duration - 0.01) {
      this.pause()
      this.offset = this.duration
      this.onEnded?.()
    }
  }

  setVolume(name: string, v: number): void {
    const t = this.tracks.get(name)
    if (t) t.volume = v
    this.applyGains()
  }

  setMuted(name: string, muted: boolean): void {
    const t = this.tracks.get(name)
    if (t) t.muted = muted
    this.applyGains()
  }

  setSolo(name: string, solo: boolean): void {
    const t = this.tracks.get(name)
    if (t) t.solo = solo
    this.applyGains()
  }

  setMaster(v: number): void {
    this.master.gain.setTargetAtTime(v, this.ctx.currentTime, RAMP)
  }

  setMode(mode: 'stems' | 'original'): void {
    this.mode = mode
    this.applyGains()
  }

  stop(): void {
    this.stopSources()
    this.playing = false
    this.offset = 0
  }

  dispose(): void {
    this.abort?.abort()
    this.stop()
    this.ctx.close()
  }

  private stopSources(): void {
    for (const t of this.tracks.values()) {
      try {
        t.source?.stop()
      } catch {
        /* never started */
      }
      t.source?.disconnect()
      t.source = null
    }
  }

  private applyGains(immediate = false): void {
    const stems = [...this.tracks.entries()].filter(([n]) => n !== ORIGINAL)
    const anySolo = stems.some(([, t]) => t.solo)
    const hasOriginal = this.tracks.has(ORIGINAL)
    for (const [name, t] of this.tracks) {
      let g: number
      if (name === ORIGINAL) g = this.mode === 'original' ? 1 : 0
      else if (this.mode === 'original' && hasOriginal) g = 0
      else g = t.muted || (anySolo && !t.solo) ? 0 : t.volume
      if (immediate) t.gain.gain.value = g
      else t.gain.gain.setTargetAtTime(g, this.ctx.currentTime, RAMP)
    }
  }
}
