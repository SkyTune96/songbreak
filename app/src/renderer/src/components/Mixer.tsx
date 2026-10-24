import type { TrackState } from '../player'
import { stemColor } from '../util'
import { Waveform } from './Waveform'

interface Props {
  stems: string[]
  peaks: Record<string, number[]>
  share: Record<string, number>
  active: Record<string, boolean> // stem playing in the current section
  duration: number
  time: number
  tracks: Record<string, TrackState>
  mode: 'stems' | 'original'
  hasOriginal: boolean
  master: number
  onTrack: (name: string, patch: Partial<TrackState>) => void
  onMode: (m: 'stems' | 'original') => void
  onMaster: (v: number) => void
  onSeek: (t: number) => void
}

export function Mixer(p: Props) {
  const anySolo = p.stems.some((s) => p.tracks[s]?.solo)
  return (
    <div className="mixer">
      <div className="mixer-top">
        <div className="segmented">
          <button className={p.mode === 'stems' ? 'on' : ''} onClick={() => p.onMode('stems')}>
            Stems
          </button>
          <button
            className={p.mode === 'original' ? 'on' : ''}
            disabled={!p.hasOriginal}
            onClick={() => p.onMode('original')}
            title="A/B against the original recording"
          >
            Original
          </button>
        </div>
        <div className="mixer-hint">
          Solo a stem to hear it alone · <kbd>Space</kbd> play/pause · <kbd>←</kbd>
          <kbd>→</kbd> seek
        </div>
        <label className="master">
          Master
          <input type="range" min={0} max={1.5} step={0.01} value={p.master} onChange={(e) => p.onMaster(+e.target.value)} />
        </label>
      </div>
      {p.stems.map((name) => {
        const t = p.tracks[name] ?? { volume: 1, muted: false, solo: false }
        const audible = p.mode === 'stems' && !t.muted && (!anySolo || t.solo)
        const share = p.share[name]
        return (
          <div key={name} className={`stem-row ${audible ? '' : 'silent'}`}>
            <div className="stem-name">
              <span className={`dot ${p.active[name] ? 'live' : ''}`} style={{ background: stemColor(name) }} />
              <div>
                <div className="stem-title">{name}</div>
                <div className="stem-share">
                  {share != null ? `${share}% of energy` : ''}
                  {share != null && share < 1 ? ' · mostly bleed' : ''}
                </div>
              </div>
            </div>
            <div className="stem-buttons">
              <button className={`ms ${t.muted ? 'mute-on' : ''}`} onClick={() => p.onTrack(name, { muted: !t.muted })} title="Mute">
                M
              </button>
              <button className={`ms ${t.solo ? 'solo-on' : ''}`} onClick={() => p.onTrack(name, { solo: !t.solo })} title="Solo">
                S
              </button>
            </div>
            <div className="stem-wave">
              {p.peaks[name] && (
                <Waveform
                  peaks={p.peaks[name]}
                  duration={p.duration}
                  time={p.time}
                  color={stemColor(name)}
                  height={44}
                  dim={!audible}
                  onSeek={p.onSeek}
                />
              )}
            </div>
            <input
              className="vol"
              type="range"
              min={0}
              max={1.5}
              step={0.01}
              value={t.volume}
              onChange={(e) => p.onTrack(name, { volume: +e.target.value })}
              title={`Volume ${Math.round(t.volume * 100)}%`}
            />
          </div>
        )
      })}
    </div>
  )
}
