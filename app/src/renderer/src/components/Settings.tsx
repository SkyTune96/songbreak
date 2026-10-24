import { useEffect, useState } from 'react'
import type { BackendStatus, Settings as S, SystemInfo } from '../types'

interface Props {
  system: SystemInfo | null
  backend: BackendStatus
  onRefreshSystem: () => void
}

const LANGS: [string, string][] = [
  ['', 'Auto-detect'],
  ['en', 'English'],
  ['es', 'Spanish'],
  ['fr', 'French'],
  ['de', 'German'],
  ['it', 'Italian'],
  ['pt', 'Portuguese'],
  ['ja', 'Japanese'],
  ['ko', 'Korean'],
  ['zh', 'Chinese']
]

export function Settings({ system, backend, onRefreshSystem }: Props) {
  const [s, setS] = useState<S | null>(null)
  const [version, setVersion] = useState('')
  const [showLog, setShowLog] = useState(false)

  useEffect(() => {
    window.songbreak.settings.get().then(setS)
    window.songbreak.info().then((i: { version: string }) => setVersion(i.version))
  }, [])

  const save = async (patch: Partial<S>): Promise<void> => {
    const next = await window.songbreak.settings.set(patch)
    setS((cur) => (cur ? { ...cur, ...next } : cur))
  }

  if (!s) return null
  const p = system?.profile
  const models = system?.ollama.models ?? []

  return (
    <div className="settings">
      <h1>Settings</h1>

      <section>
        <h2>Analysis defaults</h2>
        <div className="row">
          <div>
            <div className="row-title">Stem separation</div>
            <div className="muted">6 stems adds guitar and piano. 4 stems gives cleaner vocals, drums and bass.</div>
          </div>
          <div className="segmented">
            <button className={s.stems === 6 ? 'on' : ''} onClick={() => save({ stems: 6 })}>6 stems</button>
            <button className={s.stems === 4 ? 'on' : ''} onClick={() => save({ stems: 4 })}>4 stems</button>
          </div>
        </div>
        <div className="row">
          <div>
            <div className="row-title">Lyrics language</div>
            <div className="muted">Auto-detect works well; pin it if a song gets transcribed in the wrong language.</div>
          </div>
          <select value={s.language ?? ''} onChange={(e) => save({ language: e.target.value || null })}>
            {LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="row">
          <div>
            <div className="row-title">Story model</div>
            <div className="muted">Any model pulled in Ollama. Default follows your hardware profile ({p?.llm_model ?? '…'}).</div>
          </div>
          <select value={s.llmModel ?? ''} onChange={(e) => save({ llmModel: e.target.value || null })}>
            <option value="">Profile default</option>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </section>

      <section>
        <h2>Storage</h2>
        <div className="row">
          <div>
            <div className="row-title">Library folder</div>
            <div className="muted mono">{s.libraryDir}</div>
          </div>
          <div className="btns">
            <button onClick={() => window.songbreak.shell.openPath(s.libraryDir)}>Open</button>
            <button
              onClick={async () => {
                const d = await window.songbreak.dialogs.folder('Choose the Songbreak library folder')
                if (d) save({ libraryDir: d })
              }}
            >
              Change…
            </button>
          </div>
        </div>
      </section>

      <section>
        <h2>Engine</h2>
        <div className="row">
          <div>
            <div className="row-title">Hardware</div>
            <div className="muted">
              {p ? `${p.gpu} · ${p.vram_gb} GB VRAM · ${p.ram_gb} GB RAM · profile ${p.name}` : 'Detecting…'}
              {system?.torch && ` · PyTorch ${system.torch}${system.cuda ? ` (CUDA ${system.cuda})` : ' (CPU)'}`}
            </div>
          </div>
        </div>
        <div className="row">
          <div>
            <div className="row-title">Ollama</div>
            <div className="muted">
              {system?.ollama.running ? `Running · ${models.length} model${models.length === 1 ? '' : 's'}` : 'Not running'}
              {' · '}
              <a href="https://ollama.com/download" target="_blank" rel="noreferrer">ollama.com</a>
            </div>
          </div>
          <button onClick={onRefreshSystem}>Check again</button>
        </div>
        <div className="row">
          <div>
            <div className="row-title">Python environment</div>
            <div className="muted mono">{s.pythonPath ?? `${s.managedPython} (managed by Songbreak)`}</div>
          </div>
          <div className="btns">
            <button
              onClick={async () => {
                const py = await window.songbreak.dialogs.python()
                if (py) save({ pythonPath: py })
              }}
            >
              Use another…
            </button>
            {s.pythonPath && <button onClick={() => save({ pythonPath: null })}>Use managed</button>}
          </div>
        </div>
        <div className="row">
          <div>
            <div className="row-title">Engine status</div>
            <div className="muted">
              {backend.state === 'ready' ? `Running on port ${backend.port}` : backend.state}
            </div>
          </div>
          <div className="btns">
            <button onClick={() => setShowLog(!showLog)}>{showLog ? 'Hide log' : 'Show log'}</button>
            <button onClick={() => window.songbreak.backend.restart()}>Restart engine</button>
          </div>
        </div>
        {showLog && <pre className="log">{backend.log.slice(-120).join('\n') || 'No output yet.'}</pre>}
      </section>

      <section>
        <h2>About</h2>
        <div className="row">
          <div>
            <div className="row-title">Songbreak {version}</div>
            <div className="muted">
              Open source under the MIT license ·{' '}
              <a href="https://github.com/SkyTune96/songbreak" target="_blank" rel="noreferrer">github.com/SkyTune96/songbreak</a>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
