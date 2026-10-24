import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type ChordSegment, type MashupRequest } from '../api'
import { ChordLane, chordAt } from './Chords'
import { Mix } from './Mix'
import { ORIGINAL, StemPlayer, type TrackState } from '../player'
import type { Job, Song, SystemInfo } from '../types'
import { fmtTime, letterColor, STEM_ORDER, sumPeaks } from '../util'
import { Instruments } from './Instruments'
import { Lyrics } from './Lyrics'
import { Mixer } from './Mixer'
import { Sections } from './Sections'
import { Story } from './Story'
import { Waveform } from './Waveform'

type Tab = 'mixer' | 'lyrics' | 'story' | 'instruments' | 'sections' | 'mix'

interface Props {
  songId: string
  system: SystemInfo | null
  job: Job | undefined
  jobs: Job[]
  refreshKey: number
  onReanalyze: (song: Song, full: boolean) => void
  onDelete: (song: Song) => void
  onMashup: (req: MashupRequest) => void
  onOpen: (id: string) => void
}

export function SongView({ songId, system, job, jobs, refreshKey, onReanalyze, onDelete, onMashup, onOpen }: Props) {
  const [song, setSong] = useState<Song | null>(null)
  const [peaks, setPeaks] = useState<Record<string, number[]>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(0) // 0..1 audio decode progress; 1 = ready
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [tab, setTab] = useState<Tab>('mixer')
  const [tracks, setTracks] = useState<Record<string, TrackState>>({})
  const [mode, setMode] = useState<'stems' | 'original'>('stems')
  const [master, setMaster] = useState(1)
  const [menu, setMenu] = useState(false)
  const [chords, setChords] = useState<ChordSegment[]>([])
  const [speed, setSpeed] = useState(1)
  const [preparing, setPreparing] = useState<number | null>(null)
  const [loopLabel, setLoopLabel] = useState<string | null>(null)
  const player = useRef<StemPlayer | null>(null)
  const baseUrls = useRef<Record<string, string>>({})

  // Load analysis + audio whenever the song (or its analysis, after a re-run) changes.
  useEffect(() => {
    let alive = true
    setSong(null)
    setError(null)
    setLoading(0)
    setTime(0)
    setPlaying(false)
    setMode('stems')
    setChords([])
    setSpeed(1)
    setLoopLabel(null)
    const p = new StemPlayer()
    player.current = p
    p.onEnded = () => setPlaying(false)
    ;(async () => {
      try {
        const [s, pk] = await Promise.all([api.song(songId), api.peaks(songId)])
        if (!alive) return
        setSong(s)
        setPeaks(pk)
        const urls: Record<string, string> = {}
        for (const [name, rel] of Object.entries(s.stems ?? {})) urls[name] = api.fileUrl(songId, rel)
        if (s.source_file) urls[ORIGINAL] = api.fileUrl(songId, s.source_file)
        baseUrls.current = urls
        api.chords(songId).then((c) => alive && setChords(c.segments)).catch(() => undefined)
        if (!Object.keys(s.stems ?? {}).length && s.source_file) setMode('original')
        await p.load(urls, (f) => alive && setLoading(Math.min(f, 0.99)))
        if (!alive) return
        if (!Object.keys(s.stems ?? {}).length) p.setMode('original')
        const st: Record<string, TrackState> = {}
        for (const name of Object.keys(s.stems ?? {})) st[name] = p.state(name)!
        setTracks(st)
        setLoading(1)
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    })()
    return () => {
      alive = false
      p.dispose()
    }
  }, [songId, refreshKey])

  // Drive the playhead from the audio clock.
  useEffect(() => {
    let raf = 0
    const loop = (): void => {
      const p = player.current
      if (p) {
        p.tick()
        setTime(p.currentTime)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [])

  const toggle = useCallback(() => {
    player.current?.toggle()
    setPlaying(!!player.current?.playing)
  }, [])
  const seek = useCallback((t: number) => player.current?.seek(t), [])

  // Practice speed: swap in pitch-preserving stretched stems (rendered once per speed, then cached).
  const changeSpeed = async (s: number): Promise<void> => {
    const p = player.current
    if (!p || s === speed) return
    const pos = p.currentTime
    const was = p.playing
    setPreparing(s)
    try {
      let urls = baseUrls.current
      if (s !== 1) {
        const r = await api.practice(songId, s)
        urls = Object.fromEntries(Object.entries(r.stems).map(([n, rel]) => [n, api.fileUrl(songId, rel)]))
      }
      p.pause()
      await p.load(urls, undefined, s)
      if (s !== 1) {
        p.setMode('stems')
        setMode('stems')
      }
      p.seek(pos)
      if (was) p.play()
      setSpeed(s)
    } catch (e) {
      alert(`Couldn't prepare ${Math.round(s * 100)}% speed: ${(e as Error).message}`)
    } finally {
      setPreparing(null)
    }
  }

  const loopSection = useCallback(() => {
    const p = player.current
    const st = p && song?.structure
    if (!p || !st) return
    if (p.looping) {
      p.setLoop(null)
      setLoopLabel(null)
      return
    }
    const sec = st.sections.find((x) => p.currentTime >= x.start && p.currentTime < x.end)
    if (sec) {
      p.setLoop([sec.start, sec.end])
      setLoopLabel(sec.label)
    }
  }, [song])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.target as HTMLElement).closest('input, textarea, select')) return
      if (e.code === 'Space') {
        e.preventDefault()
        toggle()
      } else if (e.code === 'ArrowRight') seek((player.current?.currentTime ?? 0) + 5)
      else if (e.code === 'ArrowLeft') seek((player.current?.currentTime ?? 0) - 5)
      else if (e.code === 'KeyL') loopSection()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle, seek, loopSection])

  const updateTrack = (name: string, patch: Partial<TrackState>): void => {
    const p = player.current!
    if (patch.volume != null) p.setVolume(name, patch.volume)
    if (patch.muted != null) p.setMuted(name, patch.muted)
    if (patch.solo != null) p.setSolo(name, patch.solo)
    setTracks((t) => ({ ...t, [name]: { ...t[name], ...patch } }))
  }

  const st = song?.structure
  const duration = st?.duration ?? player.current?.duration ?? 0
  const stems = useMemo(
    () => STEM_ORDER.filter((s) => song?.stems?.[s]).concat(Object.keys(song?.stems ?? {}).filter((s) => !STEM_ORDER.includes(s))),
    [song]
  )
  const section = st?.sections.find((s) => time >= s.start && time < s.end)
  const active = Object.fromEntries(stems.map((s) => [s, !!section?.stems_active.includes(s)]))
  const busy = !!job && (job.status === 'queued' || job.status === 'running')
  const tags = song?.instruments?.song

  if (error) return <div className="song-view"><div className="error-box">Couldn't open this song: {error}</div></div>
  if (!song) return <div className="song-view"><div className="loading-big">Loading…</div></div>

  return (
    <div className="song-view">
      <header className="song-header">
        <div>
          <h1>{song.title}</h1>
          <div className="meta">
            {st && (
              <>
                <span>{fmtTime(st.duration)}</span>
                <span>{Math.round(st.tempo_bpm)} BPM</span>
                <span>{st.key}</span>
              </>
            )}
            {tags?.genre.slice(0, 2).map(([g]) => <span key={g} className="chip">{g}</span>)}
            {tags?.mood.slice(0, 2).map(([m]) => <span key={m} className="chip mood">{m}</span>)}
          </div>
        </div>
        <div className="header-actions">
          {busy && (
            <span className="badge running">
              {job!.status === 'queued' ? 'Queued' : `${job!.stage ?? 'Working'} ${Math.round(job!.progress * 100)}%`}
            </span>
          )}
          <button onClick={() => window.songbreak.shell.openPath(song.dir)}>Open folder</button>
          <div className="menu-wrap">
            <button disabled={busy} onClick={() => setMenu(!menu)}>
              {song.kind === 'mashup' ? 'More ▾' : 'Re-analyze ▾'}
            </button>
            {menu && (
              <div className="menu" onMouseLeave={() => setMenu(false)}>
                {song.kind !== 'mashup' && (
                  <>
                    <button onClick={() => (setMenu(false), onReanalyze(song, false))}>
                      <strong>Quick</strong>
                      <span>Reuse stems and lyrics; redo structure, instruments and story</span>
                    </button>
                    <button onClick={() => (setMenu(false), onReanalyze(song, true))}>
                      <strong>Full</strong>
                      <span>Separate and transcribe again from scratch</span>
                    </button>
                  </>
                )}
                <button className="danger" onClick={() => (setMenu(false), onDelete(song))}>
                  <strong>Delete</strong>
                  <span>Remove this song's analysis and stems</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="transport">
        <button className="play" onClick={toggle} disabled={loading < 1} title="Play / pause (Space)">
          {playing ? (
            <svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
          ) : (
            <svg viewBox="0 0 24 24"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" /></svg>
          )}
        </button>
        <div className="clock">
          <span>{fmtTime(time)}</span>
          <span className="muted"> / {fmtTime(duration)}</span>
        </div>
        <div className="now-section">
          {section && (
            <>
              <span className="letter" style={{ background: letterColor(section.letter) }}>{section.letter}</span>
              {section.label}
            </>
          )}
          {chordAt(chords, time) && <span className="now-chord" title="Current chord">{chordAt(chords, time)}</span>}
          {speed !== 1 && <span className="badge">{Math.round(speed * 100)}% speed</span>}
          {loopLabel && <span className="badge">⟲ {loopLabel}</span>}
        </div>
        {loading < 1 && (
          <div className="loading-audio">
            <span>Loading audio</span>
            <span className="progress"><span style={{ width: `${loading * 100}%` }} /></span>
          </div>
        )}
      </div>

      <div className="main-wave">
        <ChordLane chords={chords} duration={duration} time={time} onSeek={seek} />
        <Waveform peaks={sumPeaks(peaks)} duration={duration} time={time} color="#e8eaf2" height={120} sections={st?.sections} onSeek={seek} />
      </div>

      <nav className="tabs">
        {(
          [
            ['mixer', 'Mixer'],
            ['lyrics', 'Lyrics'],
            ['story', 'Story'],
            ['instruments', 'Instruments'],
            ['sections', 'Sections'],
            ['mix', song.kind === 'mashup' ? 'Mashup' : 'Mix']
          ] as [Tab, string][]
        ).map(([id, label]) => (
          <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>
            {label}
            {id === 'story' && song.story?.llm && <span className="tab-dot" />}
          </button>
        ))}
      </nav>

      <div className="panel">
        {tab === 'mixer' && (
          <Mixer
            stems={stems}
            peaks={peaks}
            share={st?.stem_share_pct ?? {}}
            active={active}
            duration={duration}
            time={time}
            tracks={tracks}
            mode={mode}
            hasOriginal={!!song.source_file}
            master={master}
            onTrack={updateTrack}
            onMode={(m) => (player.current?.setMode(m), setMode(m))}
            onMaster={(v) => (player.current?.setMaster(v), setMaster(v))}
            onSeek={seek}
            speed={speed}
            preparing={preparing}
            onSpeed={changeSpeed}
            loop={loopLabel}
            currentSection={section?.label ?? null}
            onLoop={loopSection}
            onClearLoop={loopSection}
          />
        )}
        {tab === 'lyrics' && (
          <Lyrics lines={song.lyrics?.lines ?? []} sections={st?.sections ?? []} chords={chords} time={time} playing={playing} onSeek={seek} />
        )}
        {tab === 'story' && (
          <Story song={song} ollamaRunning={!!system?.ollama.running} busy={busy} onGenerate={() => onReanalyze(song, false)} />
        )}
        {tab === 'instruments' && <Instruments song={song} onSeek={seek} />}
        {tab === 'sections' && <Sections song={song} time={time} onSeek={seek} />}
        {tab === 'mix' && (
          <Mix
            song={song}
            busy={busy}
            jobs={jobs}
            onOpen={onOpen}
            onMashup={(vocals, instrumental, opts) => onMashup({ vocals, instrumental, ...opts })}
          />
        )}
      </div>
    </div>
  )
}
