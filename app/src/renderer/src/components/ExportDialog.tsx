import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Song } from '../types'
import { STEM_ORDER, stemColor } from '../util'

const LAST_DIR = 'songbreak.exportDir'

function lastDir(): string | null {
  try {
    return localStorage.getItem(LAST_DIR)
  } catch {
    return null
  }
}

export function ExportDialog({ song, onClose }: { song: Song; onClose: () => void }) {
  const stems = STEM_ORDER.filter((s) => song.stems?.[s]).concat(Object.keys(song.stems ?? {}).filter((s) => !STEM_ORDER.includes(s)))
  const [picked, setPicked] = useState<Set<string>>(new Set(stems))
  const [original, setOriginal] = useState(false)
  const [format, setFormat] = useState<'wav' | 'flac'>('wav')
  const [split, setSplit] = useState(false)
  const [dir, setDir] = useState<string | null>(lastDir())
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ folder: string; project: string | null; files: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const sections = song.structure?.sections.length ?? 0

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const choose = async (): Promise<string | null> => {
    const d = await window.songbreak.dialogs.folder('Export to folder')
    if (d) {
      setDir(d)
      try {
        localStorage.setItem(LAST_DIR, d)
      } catch {
        /* storage unavailable: the folder just won't be remembered */
      }
    }
    return d
  }

  const run = async (): Promise<void> => {
    const dest = dir ?? (await choose())
    if (!dest) return
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.exportSong(song.id, {
          dest,
          stems: stems.filter((s) => picked.has(s)),
          format,
          split_sections: split,
          include_original: original
        })
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const all = picked.size === stems.length
  return (
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal export" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">Export “{song.title}”</h2>
        {result ? (
          <>
            <p>
              Exported {result.files.length} file{result.files.length === 1 ? '' : 's'} to <span className="mono">{result.folder}</span>.
              Every stem starts at 0:00, so they line up when dropped into any editor.
            </p>
            <div className="btns modal-actions">
              <button onClick={onClose}>Done</button>
              {result.project && (
                <button onClick={() => window.songbreak.shell.openPath(result.project!)} title="Opens with REAPER if it's installed">
                  Open REAPER project
                </button>
              )}
              <button className="primary" onClick={() => window.songbreak.shell.openPath(result.folder)}>Open folder</button>
            </div>
          </>
        ) : (
          <>
            <div className="row-inline">
              <div className="field-label">Stems</div>
              <button className="link" onClick={() => setPicked(all ? new Set() : new Set(stems))}>{all ? 'Select none' : 'Select all'}</button>
            </div>
            <div className="export-stems">
              {stems.map((s) => (
                <label key={s} className={`stem-check ${picked.has(s) ? 'on' : ''}`} style={{ borderColor: picked.has(s) ? stemColor(s) : undefined }}>
                  <input
                    type="checkbox"
                    checked={picked.has(s)}
                    onChange={(e) =>
                      setPicked((p) => {
                        const n = new Set(p)
                        if (e.target.checked) n.add(s)
                        else n.delete(s)
                        return n
                      })
                    }
                  />
                  <span className="dot" style={{ background: stemColor(s), opacity: 1 }} />
                  {s}
                </label>
              ))}
              {song.source_file && (
                <label className={`stem-check ${original ? 'on' : ''}`}>
                  <input type="checkbox" checked={original} onChange={(e) => setOriginal(e.target.checked)} />
                  original mix
                </label>
              )}
            </div>

            <div className="field-label">Options</div>
            <div className="export-options">
              <div className="segmented">
                <button className={format === 'wav' ? 'on' : ''} onClick={() => setFormat('wav')}>WAV</button>
                <button className={format === 'flac' ? 'on' : ''} onClick={() => setFormat('flac')}>FLAC</button>
              </div>
              <label className="pick">
                <input type="checkbox" checked={split} disabled={!sections} onChange={(e) => setSplit(e.target.checked)} />
                Also split every stem by section ({sections} sections, e.g. “03 Chorus 1 / Chorus 1 - vocals”)
              </label>
            </div>
            <p className="muted small">
              Also included: <span className="mono">lyrics.lrc</span>, a chord chart, section markers (CSV) and a{' '}
              <strong>REAPER project</strong> with each stem on its own track, the tempo set and sections as regions.
            </p>

            <div className="row-inline">
              <div className="field-label">Folder</div>
              <span className="mono small export-dir">{dir ?? 'not chosen yet'}</span>
              <button className="link" onClick={choose}>Change…</button>
            </div>
            {error && <div className="error-box">{error}</div>}
            <div className="btns modal-actions">
              <button onClick={onClose} disabled={busy}>Cancel</button>
              <button className="primary" disabled={busy || (!picked.size && !original)} onClick={run}>
                {busy ? 'Exporting…' : `Export ${picked.size + (original ? 1 : 0)} track${picked.size + (original ? 1 : 0) === 1 ? '' : 's'}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
