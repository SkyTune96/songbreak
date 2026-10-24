import { app } from 'electron'
import { ChildProcess, spawn, spawnSync } from 'child_process'
import { EventEmitter } from 'events'
import { join, resolve } from 'path'
import { loadSettings, resolvePython } from './settings'

export type BackendState =
  | { state: 'starting'; log: string[] }
  | { state: 'ready'; port: number; library: string; log: string[] }
  | { state: 'missing'; reason: string; log: string[] }
  | { state: 'crashed'; reason: string; log: string[] }

/** Folder that contains the `songbreak` Python package. */
export function pythonSourceDir(): string {
  return app.isPackaged ? join(process.resourcesPath, 'python') : resolve(app.getAppPath(), '..')
}

const MAX_LOG = 400

class Backend extends EventEmitter {
  private proc: ChildProcess | null = null
  private log: string[] = []
  status: BackendState = { state: 'starting', log: [] }

  private set(status: BackendState): void {
    this.status = status
    this.emit('status', status)
  }

  private push(line: string): void {
    this.log.push(line)
    if (this.log.length > MAX_LOG) this.log.splice(0, this.log.length - MAX_LOG)
  }

  start(): void {
    this.stop()
    this.log = []
    const settings = loadSettings()
    const python = resolvePython(settings)
    if (!python) {
      this.set({ state: 'missing', reason: 'No Python environment found for Songbreak yet.', log: [] })
      return
    }
    this.set({ state: 'starting', log: [] })
    const proc = spawn(
      python,
      ['-m', 'songbreak', 'serve', '--library', settings.libraryDir, '--parent-pid', String(process.pid)],
      {
        cwd: pythonSourceDir(),
        env: {
          ...process.env,
          PYTHONPATH: pythonSourceDir(),
          PYTHONUNBUFFERED: '1',
          PYTHONIOENCODING: 'utf-8'
        },
        windowsHide: true
      }
    )
    this.proc = proc
    let buffer = ''
    const onData = (chunk: Buffer): void => {
      buffer += chunk.toString('utf-8')
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const ready = line.match(/^SONGBREAK_READY (.+)$/)
        if (ready) {
          const info = JSON.parse(ready[1])
          this.waitHealthy(info.port).then((ok) =>
            ok && this.proc === proc
              ? this.set({ state: 'ready', port: info.port, library: info.library, log: this.log })
              : undefined
          )
        } else if (line.trim()) {
          this.push(line)
          this.emit('log', line)
        }
      }
    }
    proc.stdout?.on('data', onData)
    proc.stderr?.on('data', onData)
    proc.on('error', (err) => this.set({ state: 'crashed', reason: err.message, log: this.log }))
    proc.on('exit', (code) => {
      if (this.proc !== proc) return // replaced by a restart
      this.proc = null
      const missingDeps = this.log.some((l) => /ModuleNotFoundError|No module named/.test(l))
      this.set(
        missingDeps
          ? { state: 'missing', reason: 'The Python environment is missing packages Songbreak needs.', log: this.log }
          : { state: 'crashed', reason: `The analysis engine stopped (exit code ${code}).`, log: this.log }
      )
    })
  }

  private async waitHealthy(port: number): Promise<boolean> {
    for (let i = 0; i < 120; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/api/health`)
        if (r.ok) return true
      } catch {
        /* not listening yet */
      }
      await new Promise((r) => setTimeout(r, 250))
    }
    return false
  }

  stop(): void {
    const proc = this.proc
    this.proc = null
    if (proc?.pid) {
      // Kill the whole tree: a running job may have spawned ffmpeg.
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(proc.pid), '/T', '/F'])
      else proc.kill()
    }
  }
}

export const backend = new Backend()
