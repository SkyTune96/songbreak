import type { SongbreakApi } from '../../preload'

declare global {
  interface Window {
    songbreak: SongbreakApi
  }
}

export type Scored = [string, number]

export interface Word {
  word: string
  start: number
  end: number
}

export interface Line {
  start: number
  end: number
  text: string
  words: Word[]
}

export interface Section {
  start: number
  end: number
  label: string
  label_auto?: string
  letter: string
  energy: number
  loudness_db: number
  lyrics: string
  vocal: boolean
  stems_active: string[]
  stem_activity: Record<string, number>
}

export interface SectionTags {
  genre: Scored[]
  mood: Scored[]
  instrument: Scored[]
  vocal: Scored[]
}

export interface StoryBeat {
  section?: string
  beat?: string
  what_happens?: string
  emotion?: string
}

export interface StoryLLM {
  one_line_summary?: string
  song_form?: string
  story_mode?: string
  narrator?: string
  addressee?: string | null
  characters?: string[]
  setting?: string | null
  central_conflict?: string
  arc?: StoryBeat[]
  turning_point?: string
  resolution?: string
  themes?: string[]
  devices?: string[]
  music_and_story?: string
}

export interface Heuristics {
  song_form: string
  form_letters: string
  section_sequence: string[]
  point_of_view: string
  pronoun_counts: Record<string, number>
  lyric_repetition_pct: number
  energy_arc: string
  climax_section: string | null
  climax_time: number | null
}

export interface Profile {
  name: string
  device: string
  gpu: string
  vram_gb: number
  ram_gb: number
  demucs_4stem: string
  demucs_shifts: number
  whisper_model: string
  llm_model: string
}

export interface MashupInfo {
  vocals_from: { id: string; title: string; tempo: number; key: string }
  instrumental_from: { id: string; title: string; tempo: number; key: string }
  stretch: number
  semitones: number
  offset_bars: number
  vocal_offset_s: number
  engine: 'rubberband' | 'signalsmith' | 'librosa' | 'none'
  vocal_key_after: string | null
}

export interface Song {
  id: string
  dir: string
  title: string
  kind?: 'song' | 'mashup'
  mashup?: MashupInfo
  source: string
  source_file: string | null
  analyzed_at?: string
  profile: Profile
  options?: Record<string, unknown>
  stems?: Record<string, string>
  lyrics?: { model: string; language: string; lines: Line[]; text: string }
  structure?: {
    duration: number
    tempo_bpm: number
    beats: number[]
    key: string
    key_confidence: number
    form_letters: string
    sections: Section[]
    stem_share_pct: Record<string, number>
    loudness_curve_db: number[]
  }
  instruments?: {
    model: string
    song: { genre: Scored[]; mood: Scored[]; instruments: Scored[]; vocal_style: Scored[] }
    sections: SectionTags[]
  }
  story?: { heuristics: Heuristics; llm?: StoryLLM; llm_model?: string; note?: string }
  timings?: Record<string, { seconds: number; peak_vram_gb: number }>
}

export interface SongSummary {
  id: string
  title: string
  duration: number | null
  tempo: number | null
  key: string | null
  genre: string[]
  sections: number
  has_lyrics: boolean
  has_story: boolean
  analyzed_at: string | null
  summary: string | null
  kind: 'song' | 'mashup'
}

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export interface Job {
  id: string
  kind: 'analyze' | 'mashup' | 'playlist'
  song_id: string
  title: string
  source: string
  status: JobStatus
  stage: string | null
  stage_progress: number
  progress: number
  message: string
  error: string | null
  created: number
  started: number | null
  finished: number | null
}

export interface SystemInfo {
  ready: boolean
  profile?: Profile
  torch?: string
  cuda?: string
  cuda_available?: boolean
  ollama: { running: boolean; models: string[] }
  library: string
  version: string
}

export type BackendStatus =
  | { state: 'starting'; log: string[] }
  | { state: 'ready'; port: number; library: string; log: string[] }
  | { state: 'missing'; reason: string; log: string[] }
  | { state: 'crashed'; reason: string; log: string[] }

export interface Settings {
  pythonPath: string | null
  managedPython: string
  libraryDir: string
  stems: 4 | 6
  language: string | null
  llmModel: string | null
  ollamaHost: string
}

export interface SetupEvent {
  step: number
  total: number
  label: string
  line?: string
  done?: boolean
  error?: string
}
