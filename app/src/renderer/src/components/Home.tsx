import type { SystemInfo } from '../types'
import { Logo } from './Logo'

export function Home({ system, onAdd }: { system: SystemInfo | null; onAdd: () => void }) {
  const p = system?.profile
  return (
    <div className="home">
      <div className="drop-hero" onClick={onAdd}>
        <Logo size={64} />
        <h1>Drop a song to break it down</h1>
        <p>
          MP3, WAV, FLAC, M4A and more. Songbreak separates the instruments, transcribes the lyrics, maps the
          sections and reads the story, all on your own machine.
        </p>
        <button className="primary big">Choose files</button>
      </div>

      <div className="home-cards">
        <div className="card">
          <div className="field-label">Your rig</div>
          {p ? (
            <>
              <div className="card-big">{p.gpu}</div>
              <div className="muted">
                {p.vram_gb} GB VRAM · {p.ram_gb} GB RAM · profile <strong>{p.name}</strong>
              </div>
            </>
          ) : (
            <div className="muted">Detecting hardware…</div>
          )}
        </div>
        <div className="card">
          <div className="field-label">Models for this profile</div>
          {p ? (
            <ul className="plain">
              <li>Stems: Demucs htdemucs_6s / {p.demucs_4stem}</li>
              <li>Lyrics: {p.whisper_model.replace('openai/', '')}</li>
              <li>Story: {p.llm_model} via Ollama</li>
            </ul>
          ) : (
            <div className="muted">…</div>
          )}
        </div>
        <div className="card">
          <div className="field-label">Story engine</div>
          {system ? (
            system.ollama.running ? (
              <>
                <div className="card-big ok">Ollama running</div>
                <div className="muted">{system.ollama.models.join(', ') || 'no models pulled'}</div>
              </>
            ) : (
              <>
                <div className="card-big warn">Ollama not running</div>
                <div className="muted">Stories fall back to heuristics until it's started.</div>
              </>
            )
          ) : (
            <div className="muted">…</div>
          )}
        </div>
      </div>
    </div>
  )
}
