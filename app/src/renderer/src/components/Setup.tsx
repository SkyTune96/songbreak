import { useEffect, useRef, useState } from 'react'
import type { BackendStatus, SetupEvent } from '../types'
import { Logo } from './Logo'

/** First run (or a broken environment): install the Python engine, or point at an existing one. */
export function Setup({ backend }: { backend: BackendStatus }) {
  const [event, setEvent] = useState<SetupEvent | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const logRef = useRef<HTMLPreElement>(null)

  useEffect(
    () =>
      window.songbreak.setup.onEvent((e) => {
        const ev = e as SetupEvent
        setEvent(ev)
        if (ev.line) setLines((l) => [...l.slice(-400), ev.line!])
        if (ev.done || ev.error) setRunning(false)
      }),
    []
  )
  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight)
  }, [lines])

  const install = (): void => {
    setRunning(true)
    setLines([])
    window.songbreak.setup.run()
  }

  const reason = backend.state === 'missing' || backend.state === 'crashed' ? backend.reason : ''
  const crashed = backend.state === 'crashed'

  return (
    <div className="setup">
      <div className="setup-card">
        <Logo size={56} />
        <h1>{crashed ? 'The analysis engine stopped' : 'Set up the analysis engine'}</h1>
        <p className="muted">{reason}</p>
        {!crashed && (
          <p>
            Songbreak runs its AI models locally with Python and PyTorch. The automatic install creates a private
            environment (about 5 GB including CUDA) and downloads the models on first use. It needs Python 3.10–3.13
            installed.
          </p>
        )}

        {event && (
          <div className="setup-progress">
            <div className="setup-step">
              {event.error ? '⚠ ' : event.done ? '✓ ' : ''}
              Step {Math.min(event.step, event.total)} of {event.total}: {event.label}
            </div>
            <div className="progress"><span style={{ width: `${(event.done ? 1 : (event.step - 1) / event.total) * 100}%` }} /></div>
            {event.error && <div className="error-box">{event.error}</div>}
          </div>
        )}

        <div className="btns">
          {crashed ? (
            <button className="primary" onClick={() => window.songbreak.backend.restart()}>Restart engine</button>
          ) : (
            <button className="primary" disabled={running} onClick={install}>
              {running ? 'Installing…' : event?.error ? 'Try again' : 'Install automatically'}
            </button>
          )}
          <button
            disabled={running}
            onClick={async () => {
              const py = await window.songbreak.dialogs.python()
              if (py) await window.songbreak.settings.set({ pythonPath: py })
            }}
          >
            Use an existing environment…
          </button>
        </div>

        {(lines.length > 0 || (crashed && backend.log.length > 0)) && (
          <pre className="log" ref={logRef}>{(lines.length ? lines : backend.log.slice(-80)).join('\n')}</pre>
        )}
      </div>
    </div>
  )
}
