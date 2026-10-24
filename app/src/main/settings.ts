import { app } from 'electron'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'

export interface Settings {
  pythonPath: string | null // null = the venv the app manages itself
  libraryDir: string
  stems: 4 | 6
  language: string | null
  llmModel: string | null // null = pick by hardware profile
  ollamaHost: string
}

const file = (): string => join(app.getPath('userData'), 'settings.json')

export function managedVenvDir(): string {
  // Short on purpose: torch's CUDA DLL paths break Windows' 260-char limit under deep folders.
  return join(process.env.LOCALAPPDATA ?? app.getPath('userData'), 'Songbreak', 'venv')
}

export function managedPython(): string {
  return process.platform === 'win32'
    ? join(managedVenvDir(), 'Scripts', 'python.exe')
    : join(managedVenvDir(), 'bin', 'python')
}

function defaults(): Settings {
  return {
    pythonPath: null,
    libraryDir: join(app.getPath('music'), 'Songbreak'),
    stems: 6,
    language: null,
    llmModel: null,
    ollamaHost: 'http://localhost:11434'
  }
}

export function loadSettings(): Settings {
  try {
    return { ...defaults(), ...JSON.parse(readFileSync(file(), 'utf-8')) }
  } catch {
    return defaults()
  }
}

export function saveSettings(patch: Partial<Settings>): Settings {
  const next = { ...loadSettings(), ...patch }
  mkdirSync(app.getPath('userData'), { recursive: true })
  writeFileSync(file(), JSON.stringify(next, null, 2))
  return next
}

/** The Python interpreter to run the backend with, in priority order. */
export function resolvePython(s: Settings): string | null {
  const candidates = [process.env.SONGBREAK_PYTHON, s.pythonPath, managedPython()]
  return candidates.find((p): p is string => !!p && existsSync(p)) ?? null
}
