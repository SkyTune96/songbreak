import type { Job, Song, SongSummary, SystemInfo } from './types'

let base = ''

export function setPort(port: number): void {
  base = `http://127.0.0.1:${port}`
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(base + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) }
  })
  if (!r.ok) {
    let detail = r.statusText
    try {
      detail = (await r.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return r.json()
}

export interface JobOptions {
  stems?: 4 | 6
  language?: string | null
  llm_model?: string | null
  ollama_host?: string
  force?: boolean
  skip?: string[]
}

export interface Match {
  id: string
  title: string
  score: number
  has_vocals: boolean
  tempo: { from: number | null; to: number | null; stretch: number; score: number }
  key: { from: string | null; to: string | null; camelot: [string | null, string | null]; distance: number; shift: number }
  energy: number
  shared_themes: string[]
}

export interface MashupRequest {
  vocals: string
  instrumental: string
  semitones?: number | null
  offset_bars?: number
  song_id?: string
}

export const api = {
  matches: (id: string) => req<Match[]>(`/api/songs/${encodeURIComponent(id)}/matches`),
  mashup: (body: MashupRequest) => req<Job>('/api/mashups', { method: 'POST', body: JSON.stringify(body) }),
  system: (ollamaHost?: string) =>
    req<SystemInfo>(`/api/system${ollamaHost ? `?ollama_host=${encodeURIComponent(ollamaHost)}` : ''}`),
  library: () => req<SongSummary[]>('/api/library'),
  song: (id: string) => req<Song>(`/api/songs/${encodeURIComponent(id)}`),
  peaks: (id: string) => req<Record<string, number[]>>(`/api/songs/${encodeURIComponent(id)}/peaks`),
  deleteSong: (id: string) => req<{ ok: boolean }>(`/api/songs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  jobs: () => req<Job[]>('/api/jobs'),
  jobLog: (id: string) => req<{ log: string[] }>(`/api/jobs/${id}/log`),
  submit: (path: string, options: JobOptions, songId?: string) =>
    req<Job>('/api/jobs', { method: 'POST', body: JSON.stringify({ path, options, song_id: songId ?? null }) }),
  cancel: (id: string) => req<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  fileUrl: (id: string, rel: string) =>
    `${base}/api/songs/${encodeURIComponent(id)}/file/${rel.split(/[\\/]/).map(encodeURIComponent).join('/')}`,
  events: () => new EventSource(`${base}/api/events`)
}
