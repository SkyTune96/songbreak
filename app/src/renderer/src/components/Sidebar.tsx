import { useEffect, useRef, useState } from 'react'
import type { FolderData, Playlist } from '../api'
import type { Job, SongSummary, SystemInfo } from '../types'
import { fmtTime } from '../util'
import { Logo } from './Logo'

interface Props {
  songs: SongSummary[]
  folders: FolderData
  jobs: Job[]
  selected: string | null
  view: string
  system: SystemInfo | null
  organizing: boolean
  renameId: string | null
  onSelect: (id: string) => void
  onAdd: () => void
  onCancel: (job: Job) => void
  onDismiss: (job: Job) => void
  onSettings: () => void
  onHome: () => void
  onNewFolder: (songIds: string[]) => void
  onStartRename: (id: string) => void
  onRenameFolder: (id: string, name: string) => void
  onRenameDone: () => void
  onDeleteFolder: (id: string, name: string) => void
  onMove: (songId: string, folderId: string | null) => void
  onDeleteSong: (song: SongSummary) => void
  onOrganize: () => void
  playlists: Playlist[]
  selectedPlaylist: string | null
  onSelectPlaylist: (id: string) => void
  onNewPlaylist: (folderId: string | null) => void
}

const STAGE_NAMES: Record<string, string> = {
  load: 'Decoding',
  stems: 'Separating stems',
  lyrics: 'Transcribing lyrics',
  structure: 'Mapping structure',
  instruments: 'Naming instruments',
  story: 'Writing the story',
  peaks: 'Finishing',
  mashup: 'Rendering mashup',
  playlist: 'Building story playlist'
}
const DRAG_TYPE = 'application/x-songbreak-song'
const COLLAPSE_KEY = 'songbreak.collapsedFolders'

function loadCollapsed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? '[]'))
  } catch {
    return new Set()
  }
}

export function Sidebar(p: Props) {
  const [q, setQ] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed)
  const [menu, setMenu] = useState<{ x: number; y: number; song: SongSummary } | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const visibleJobs = p.jobs.filter((j) => j.status !== 'done' && j.status !== 'cancelled')
  const profile = p.system?.profile

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed]))
    } catch {
      /* storage unavailable: collapse state just won't persist */
    }
  }, [collapsed])

  useEffect(() => {
    if (!menu) return
    const close = (): void => setMenu(null)
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('click', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu])

  const toggle = (id: string): void =>
    setCollapsed((c) => {
      const n = new Set(c)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const songItem = (s: SongSummary) => (
    <button
      key={s.id}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, s.id)
        e.dataTransfer.effectAllowed = 'move'
      }}
      className={`song-item ${p.view === 'song' && p.selected === s.id ? 'on' : ''}`}
      onClick={() => p.onSelect(s.id)}
      onContextMenu={(e) => {
        e.preventDefault()
        e.stopPropagation()
        setMenu({ x: e.clientX, y: e.clientY, song: s })
      }}
    >
      <span className="song-item-title">
        {s.kind === 'mashup' && <span className="tag">mashup</span>}
        {s.title}
      </span>
      <span className="song-item-meta">
        {fmtTime(s.duration)}
        {s.key ? ` · ${s.key}` : ''}
        {s.tempo ? ` · ${Math.round(s.tempo)} BPM` : ''}
      </span>
    </button>
  )

  const dropProps = (folderId: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_TYPE)) return
      e.preventDefault()
      setDropTarget(folderId ?? '__unfiled__')
    },
    onDragLeave: () => setDropTarget(null),
    onDrop: (e: React.DragEvent) => {
      const id = e.dataTransfer.getData(DRAG_TYPE)
      setDropTarget(null)
      if (!id) return
      e.preventDefault()
      e.stopPropagation()
      p.onMove(id, folderId)
    }
  })

  const filtered = p.songs.filter((s) => s.title.toLowerCase().includes(q.toLowerCase()))
  const byFolder = (fid: string | null): SongSummary[] =>
    filtered.filter((s) => (p.folders.assignments[s.id] ?? null) === fid)
  const unfiled = byFolder(null)
  const hasFolders = p.folders.folders.length > 0

  return (
    <aside className="sidebar">
      <div className="brand" onClick={p.onHome}>
        <Logo size={26} />
        <span>Songbreak</span>
      </div>
      <button className="primary add" onClick={p.onAdd}>
        <span className="plus">+</span> Add songs
      </button>

      {visibleJobs.length > 0 && (
        <div className="jobs">
          <div className="side-label">Queue</div>
          {visibleJobs.map((j) => (
            <div key={j.id} className={`job ${j.status}`}>
              <div className="job-head">
                <span className="job-title" title={j.source}>{j.title}</span>
                {(j.status === 'queued' || j.status === 'running') && (
                  <button className="link" onClick={() => p.onCancel(j)} title="Cancel">✕</button>
                )}
                {j.status === 'error' && (
                  <button className="link" onClick={() => p.onDismiss(j)} title="Dismiss">✕</button>
                )}
              </div>
              {j.status === 'running' && (
                <>
                  <div className="progress"><span style={{ width: `${j.progress * 100}%` }} /></div>
                  <div className="job-msg">
                    {STAGE_NAMES[j.stage ?? ''] ?? j.message} · {Math.round(j.progress * 100)}%
                  </div>
                </>
              )}
              {j.status === 'queued' && <div className="job-msg">Waiting…</div>}
              {j.status === 'error' && <div className="job-msg err" title={j.error ?? ''}>{j.error}</div>}
            </div>
          ))}
        </div>
      )}

      <div className="side-label library-head">
        <span>Story playlists</span>
        <span className="lib-actions">
          <button className="link" title="Order songs so the playlist tells a story" disabled={p.songs.filter((s) => s.kind !== 'mashup').length < 2} onClick={() => p.onNewPlaylist(null)}>
            + New
          </button>
        </span>
      </div>
      <div className="playlist-list">
        {p.playlists.map((pl) => (
          <button
            key={pl.id}
            className={`song-item ${p.view === 'playlist' && p.selectedPlaylist === pl.id ? 'on' : ''}`}
            onClick={() => p.onSelectPlaylist(pl.id)}
          >
            <span className="song-item-title">≋ {pl.name}</span>
            <span className="song-item-meta">{pl.arc_name} · {pl.songs.length} songs</span>
          </button>
        ))}
        {!p.playlists.length && <div className="muted small pad">Turn a folder into a playlist that tells a story.</div>}
      </div>

      <div className="side-label library-head">
        <span>Library</span>
        <span className="lib-actions">
          <button className="link" title="New folder" onClick={() => p.onNewFolder([])}>+ Folder</button>
          {unfiled.length > 0 && p.songs.length > 1 && (
            <button className="link" title="Sort unfiled songs into folders named from their analysis" disabled={p.organizing} onClick={p.onOrganize}>
              {p.organizing ? 'Organizing…' : '✦ Organize'}
            </button>
          )}
        </span>
      </div>
      {p.songs.length > 4 && (
        <input className="search" placeholder="Search songs" value={q} onChange={(e) => setQ(e.target.value)} />
      )}
      <div className="song-list">
        {p.folders.folders.map((f) => {
          const items = byFolder(f.id)
          if (q && !items.length) return null
          const open = !collapsed.has(f.id) || !!q
          return (
            <div key={f.id} className={`folder ${dropTarget === f.id ? 'drop' : ''}`} {...dropProps(f.id)}>
              <div className="folder-head" onClick={() => toggle(f.id)}>
                <span className={`caret ${open ? 'open' : ''}`}>▸</span>
                {p.renameId === f.id ? (
                  <RenameInput
                    value={f.name}
                    onDone={(name) => {
                      if (name && name !== f.name) p.onRenameFolder(f.id, name)
                      p.onRenameDone()
                    }}
                  />
                ) : (
                  <span className="folder-name" onDoubleClick={(e) => (e.stopPropagation(), p.onStartRename(f.id))} title="Double-click to rename">
                    {f.name}
                  </span>
                )}
                <span className="folder-count">{items.length}</span>
                <span className="folder-actions" onClick={(e) => e.stopPropagation()}>
                  <button className="link" title="Make a story playlist from this folder" onClick={() => p.onNewPlaylist(f.id)}>≋</button>
                  <button className="link" title="Rename" onClick={() => p.onStartRename(f.id)}>✎</button>
                  <button className="link" title="Delete folder (songs are kept)" onClick={() => p.onDeleteFolder(f.id, f.name)}>🗑</button>
                </span>
              </div>
              {open && (
                <div className="folder-songs">
                  {items.map(songItem)}
                  {!items.length && <div className="muted small pad">Drag songs here</div>}
                </div>
              )}
            </div>
          )
        })}
        {hasFolders && unfiled.length > 0 && (
          <div className={`folder unfiled ${dropTarget === '__unfiled__' ? 'drop' : ''}`} {...dropProps(null)}>
            <div className="folder-head plain">
              <span className="folder-name">Unfiled</span>
              <span className="folder-count">{unfiled.length}</span>
            </div>
          </div>
        )}
        <div {...(hasFolders ? dropProps(null) : {})}>{unfiled.map(songItem)}</div>
        {!p.songs.length && <div className="muted small pad">Songs you analyse appear here.</div>}
      </div>

      <div className="side-footer">
        <div className="hw" title={profile ? `${profile.gpu}\n${profile.vram_gb} GB VRAM · ${profile.ram_gb} GB RAM` : ''}>
          <span className={`led ${p.system?.ready ? 'ok' : ''}`} />
          {profile ? (
            <span>{profile.gpu.replace('NVIDIA GeForce ', '')} · {profile.name}</span>
          ) : (
            <span>Warming up…</span>
          )}
        </div>
        <button className={`icon-btn ${p.view === 'settings' ? 'on' : ''}`} onClick={p.onSettings} title="Settings">
          <svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm7.4-2.5.04-1-.04-1 2.1-1.65-2-3.46-2.5 1a7.6 7.6 0 0 0-1.73-1L14.9 3h-4l-.37 2.6a7.6 7.6 0 0 0-1.73 1l-2.5-1-2 3.46L6.4 10.7 6.36 12l.04 1.3-2.1 1.64 2 3.46 2.5-1c.53.4 1.11.74 1.73 1L10.9 21h4l.37-2.6a7.6 7.6 0 0 0 1.73-1l2.5 1 2-3.46-2.1-1.64z" /></svg>
        </button>
      </div>

      {menu && (
        <div className="ctx" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
          <div className="ctx-label">Move to</div>
          {p.folders.folders
            .filter((f) => p.folders.assignments[menu.song.id] !== f.id)
            .map((f) => (
              <button key={f.id} onClick={() => (p.onMove(menu.song.id, f.id), setMenu(null))}>{f.name}</button>
            ))}
          <button onClick={() => (p.onNewFolder([menu.song.id]), setMenu(null))}>✦ New folder (AI-named)</button>
          {p.folders.assignments[menu.song.id] && (
            <button onClick={() => (p.onMove(menu.song.id, null), setMenu(null))}>Remove from folder</button>
          )}
          <div className="ctx-sep" />
          <button className="danger" onClick={() => (p.onDeleteSong(menu.song), setMenu(null))}>
            Delete {menu.song.kind === 'mashup' ? 'mashup' : 'song'}…
          </button>
        </div>
      )}
    </aside>
  )
}

function RenameInput({ value, onDone }: { value: string; onDone: (v: string) => void }) {
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const finish = (v: string): void => {
    if (done.current) return
    done.current = true
    onDone(v.trim())
  }
  return (
    <input
      ref={ref}
      className="rename"
      defaultValue={value}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') finish(e.currentTarget.value)
        if (e.key === 'Escape') finish(value)
      }}
      onBlur={(e) => finish(e.currentTarget.value)}
    />
  )
}
