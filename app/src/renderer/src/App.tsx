import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setPort, type JobOptions } from './api'
import { Home } from './components/Home'
import { Settings } from './components/Settings'
import { Setup } from './components/Setup'
import { Sidebar } from './components/Sidebar'
import { SongView } from './components/SongView'
import type { BackendStatus, Job, Settings as S, Song, SongSummary, SystemInfo } from './types'

type View = 'home' | 'song' | 'settings'

export function App() {
  const [backend, setBackend] = useState<BackendStatus>({ state: 'starting', log: [] })
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [songs, setSongs] = useState<SongSummary[]>([])
  const [jobs, setJobs] = useState<Record<string, Job>>({})
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [view, setView] = useState<View>('home')
  const [selected, setSelected] = useState<string | null>(null)
  const [refresh, setRefresh] = useState<Record<string, number>>({})
  const [dragging, setDragging] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const ready = backend.state === 'ready'

  useEffect(() => {
    window.songbreak.backend.get().then(setBackend)
    const offStatus = window.songbreak.backend.onStatus((s) => setBackend(s as BackendStatus))
    const offLog = window.songbreak.backend.onLog((line) =>
      setBackend((b) => ({ ...b, log: [...b.log.slice(-399), line] }))
    )
    return () => {
      offStatus()
      offLog()
    }
  }, [])

  const notify = useCallback((msg: string) => {
    setToast(msg)
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 5000)
  }, [])

  const loadLibrary = useCallback(() => api.library().then(setSongs).catch(() => undefined), [])
  const loadSystem = useCallback(async () => {
    const s: S = await window.songbreak.settings.get()
    return api.system(s.ollamaHost).then(setSystem).catch(() => undefined)
  }, [])

  // Connect to the engine once it's up: library, system info, live job events.
  useEffect(() => {
    if (backend.state !== 'ready') return
    setPort(backend.port)
    loadLibrary()
    loadSystem()
    const poll = setInterval(loadSystem, 15000)
    const es = api.events()
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data)
      if (ev.type === 'hello') setJobs(Object.fromEntries((ev.jobs as Job[]).map((j) => [j.id, j])))
      else if (ev.type === 'library') loadLibrary()
      else if (ev.type === 'job') {
        const job = ev.job as Job
        setJobs((js) => ({ ...js, [job.id]: job }))
        if (job.status === 'done') {
          setRefresh((r) => ({ ...r, [job.song_id]: (r[job.song_id] ?? 0) + 1 }))
          if (!selectedRef.current) {
            setSelected(job.song_id)
            setView('song')
          }
          notify(`“${job.title}” is ready`)
        } else if (job.status === 'error') notify(`“${job.title}” failed: ${job.error}`)
      }
    }
    return () => {
      es.close()
      clearInterval(poll)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backend.state, backend.state === 'ready' ? backend.port : 0])

  // Faster polling until torch has loaded and the GPU has been probed.
  useEffect(() => {
    if (!ready || system?.ready) return
    const t = setInterval(loadSystem, 1500)
    return () => clearInterval(t)
  }, [ready, system?.ready, loadSystem])

  const submit = useCallback(
    async (paths: string[], extra: JobOptions = {}, songId?: string) => {
      const s: S = await window.songbreak.settings.get()
      const options: JobOptions = {
        stems: s.stems,
        language: s.language,
        llm_model: s.llmModel,
        ollama_host: s.ollamaHost,
        ...extra
      }
      for (const p of paths) {
        try {
          await api.submit(p, options, songId)
        } catch (e) {
          notify(`Couldn't add ${p.split(/[\\/]/).pop()}: ${(e as Error).message}`)
        }
      }
    },
    [notify]
  )

  const addViaDialog = useCallback(async () => {
    const paths = await window.songbreak.dialogs.audio()
    if (paths.length) submit(paths)
  }, [submit])

  const reanalyze = (song: Song, full: boolean): void => {
    submit([song.source], { force: full }, song.id)
  }

  const remove = async (song: Song): Promise<void> => {
    if (!confirm(`Delete “${song.title}”? Its stems and analysis will be removed (the original file is kept).`)) return
    try {
      await api.deleteSong(song.id)
      setSelected(null)
      setView('home')
      loadLibrary()
    } catch (e) {
      notify((e as Error).message)
    }
  }

  // Drag and drop files anywhere in the window.
  useEffect(() => {
    let depth = 0
    const enter = (e: DragEvent): void => {
      if (!e.dataTransfer?.types.includes('Files')) return
      depth++
      setDragging(true)
    }
    const leave = (): void => {
      depth = Math.max(0, depth - 1)
      if (!depth) setDragging(false)
    }
    const over = (e: DragEvent): void => e.preventDefault()
    const drop = (e: DragEvent): void => {
      e.preventDefault()
      depth = 0
      setDragging(false)
      const paths = [...(e.dataTransfer?.files ?? [])].map((f) => window.songbreak.pathForFile(f)).filter(Boolean)
      if (paths.length && ready) submit(paths)
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragleave', leave)
    window.addEventListener('dragover', over)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('dragover', over)
      window.removeEventListener('drop', drop)
    }
  }, [ready, submit])

  if (backend.state === 'missing' || backend.state === 'crashed') return <Setup backend={backend} />

  if (!ready)
    return (
      <div className="boot">
        <div className="spinner" />
        <div>Starting the analysis engine…</div>
      </div>
    )

  const jobList = Object.values(jobs)
    .filter((j) => !dismissed.has(j.id))
    .sort((a, b) => a.created - b.created)
  const activeJob = selected
    ? jobList.filter((j) => j.song_id === selected && (j.status === 'running' || j.status === 'queued')).pop()
    : undefined

  return (
    <div className="app">
      <Sidebar
        songs={songs}
        jobs={jobList}
        selected={selected}
        view={view}
        system={system}
        onSelect={(id) => {
          setSelected(id)
          setView('song')
        }}
        onAdd={addViaDialog}
        onCancel={(j) => api.cancel(j.id)}
        onDismiss={(j) => setDismissed((d) => new Set(d).add(j.id))}
        onSettings={() => setView('settings')}
        onHome={() => setView('home')}
      />
      <main>
        {view === 'home' && <Home system={system} onAdd={addViaDialog} />}
        {view === 'settings' && <Settings system={system} backend={backend} onRefreshSystem={loadSystem} />}
        {view === 'song' && selected && (
          <SongView
            key={selected}
            songId={selected}
            system={system}
            job={activeJob}
            refreshKey={refresh[selected] ?? 0}
            onReanalyze={reanalyze}
            onDelete={remove}
          />
        )}
      </main>
      {dragging && (
        <div className="drop-overlay">
          <div>Drop to analyse</div>
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
