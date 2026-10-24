import { spawn, spawnSync } from 'child_process'
import { existsSync, rmSync } from 'fs'
import { join } from 'path'
import { pythonSourceDir } from './backend'
import { managedPython, managedVenvDir } from './settings'

export interface SetupEvent {
  step: number
  total: number
  label: string
  line?: string
  done?: boolean
  error?: string
}

type Emit = (e: SetupEvent) => void

const TORCH = { version: '2.8.0', cuda: 'https://download.pytorch.org/whl/cu128', cpu: 'https://download.pytorch.org/whl/cpu' }

/** A system Python that torch + demucs support (3.10–3.13), as [command, args-prefix]. */
export function findBasePython(): [string, string[]] | null {
  const tries: [string, string[]][] = [
    ['py', ['-3.12']],
    ['py', ['-3.11']],
    ['py', ['-3.13']],
    ['py', ['-3.10']],
    ['python', []],
    ['python3', []]
  ]
  for (const [cmd, pre] of tries) {
    const r = spawnSync(cmd, [...pre, '-c', 'import sys; print(sys.version_info[:2])'], { encoding: 'utf-8', windowsHide: true })
    const m = r.status === 0 && r.stdout.match(/\((\d+), (\d+)\)/)
    if (m && Number(m[1]) === 3 && Number(m[2]) >= 10 && Number(m[2]) <= 13) return [cmd, pre]
  }
  return null
}

export function hasNvidiaGpu(): boolean {
  return spawnSync('nvidia-smi', ['-L'], { windowsHide: true }).status === 0
}

function run(cmd: string, args: string[], emit: (line: string) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
    const onData = (b: Buffer): void =>
      b.toString('utf-8').split(/\r?\n|\r/).filter((l) => l.trim()).forEach(emit)
    p.stdout.on('data', onData)
    p.stderr.on('data', onData)
    p.on('error', reject)
    p.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited with code ${code}`))))
  })
}

let running = false

export async function runSetup(emit: Emit): Promise<boolean> {
  if (running) return false
  running = true
  const total = 5
  let step = 0
  const stage = (label: string): ((line: string) => void) => {
    step++
    emit({ step, total, label })
    return (line) => emit({ step, total, label, line })
  }
  try {
    const base = findBasePython()
    if (!base) throw new Error('Python 3.10–3.13 was not found. Install Python 3.12 from python.org (or `winget install -e --id Python.Python.3.12`) and try again.')
    const venv = managedVenvDir()
    let log = stage('Creating the Python environment')
    if (existsSync(venv)) rmSync(venv, { recursive: true, force: true })
    await run(base[0], [...base[1], '-m', 'venv', venv], log)
    const py = managedPython()

    log = stage('Updating pip')
    await run(py, ['-m', 'pip', 'install', '--upgrade', 'pip', '--progress-bar', 'off'], log)

    const gpu = hasNvidiaGpu()
    log = stage(gpu ? 'Installing PyTorch with CUDA (about 3 GB)' : 'Installing PyTorch for CPU')
    await run(py, ['-m', 'pip', 'install', '--progress-bar', 'off', `torch==${TORCH.version}`, `torchaudio==${TORCH.version}`,
      '--index-url', gpu ? TORCH.cuda : TORCH.cpu], log)

    log = stage('Installing Demucs, Whisper and the audio libraries')
    await run(py, ['-m', 'pip', 'install', '--progress-bar', 'off', '-r', join(pythonSourceDir(), 'requirements.txt')], log)

    log = stage('Checking the installation')
    await run(py, ['-c', 'import torch, demucs, transformers, librosa, fastapi; print("torch", torch.__version__, "cuda", torch.cuda.is_available())'], log)
    emit({ step: total, total, label: 'Ready', done: true })
    return true
  } catch (e) {
    emit({ step, total, label: 'Setup failed', error: (e as Error).message })
    return false
  } finally {
    running = false
  }
}
