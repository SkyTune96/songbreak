import type { Song } from '../types'
import { beatColor, fmtTime } from '../util'

interface Props {
  song: Song
  ollamaRunning: boolean
  busy: boolean
  onGenerate: () => void
}

function Field({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div className="field">
      <div className="field-label">{label}</div>
      <div className="field-value">{value}</div>
    </div>
  )
}

export function Story({ song, ollamaRunning, busy, onGenerate }: Props) {
  const story = song.story
  if (!story) return <div className="empty-panel">No structure analysis for this song.</div>
  const h = story.heuristics
  const llm = story.llm

  return (
    <div className="story">
      {llm?.one_line_summary && <blockquote className="summary">{llm.one_line_summary}</blockquote>}

      <div className="glance">
        <div className="glance-item">
          <div className="field-label">Form</div>
          <div className="field-value">{llm?.song_form || h.song_form}</div>
          <div className="letters">
            {h.form_letters.split('').map((l, i) => (
              <span key={i}>{l}</span>
            ))}
          </div>
        </div>
        <div className="glance-item">
          <div className="field-label">Energy arc</div>
          <div className="field-value">{h.energy_arc}</div>
          {h.climax_section && (
            <div className="muted">
              Climax: {h.climax_section} at {fmtTime(h.climax_time)}
            </div>
          )}
        </div>
        <div className="glance-item">
          <div className="field-label">Point of view</div>
          <div className="field-value">{h.point_of_view}</div>
          <div className="muted">{h.lyric_repetition_pct}% of lyric lines repeat</div>
        </div>
      </div>

      {llm ? (
        <>
          <div className="fields">
            <Field label="Mode" value={llm.story_mode} />
            <Field label="Narrator" value={llm.narrator} />
            <Field label="Addressee" value={llm.addressee} />
            <Field label="Setting" value={llm.setting} />
            <Field label="Central conflict" value={llm.central_conflict} />
            <Field label="Turning point" value={llm.turning_point} />
            <Field label="Resolution" value={llm.resolution} />
            <Field label="Music & story" value={llm.music_and_story} />
          </div>

          {!!llm.arc?.length && (
            <>
              <h3>Narrative arc</h3>
              <div className="arc">
                {llm.arc.map((b, i) => (
                  <div key={i} className="beat" style={{ borderTopColor: beatColor(b.beat) }}>
                    <div className="beat-head">
                      <span>{b.section}</span>
                      <span className="chip" style={{ color: beatColor(b.beat), borderColor: beatColor(b.beat) + '66' }}>
                        {b.beat}
                      </span>
                    </div>
                    <div className="beat-body">{b.what_happens}</div>
                    {b.emotion && <div className="beat-emotion">{b.emotion}</div>}
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="chip-rows">
            {!!llm.characters?.length && <ChipRow label="Characters" items={llm.characters} />}
            {!!llm.themes?.length && <ChipRow label="Themes" items={llm.themes} />}
            {!!llm.devices?.length && <ChipRow label="Devices" items={llm.devices} />}
          </div>
          <div className="muted small">Written by {story.llm_model} running locally through Ollama.</div>
        </>
      ) : (
        <div className="callout">
          <div>
            <strong>No narrative breakdown yet.</strong>{' '}
            {story.note ??
              'Run the story stage to have a local LLM read the lyrics and write up the narrator, conflict and arc.'}
          </div>
          <button className="primary" disabled={busy || !ollamaRunning} onClick={onGenerate}>
            {busy ? 'Working…' : ollamaRunning ? 'Generate story' : 'Ollama not running'}
          </button>
        </div>
      )}
    </div>
  )
}

function ChipRow({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="chip-row">
      <div className="field-label">{label}</div>
      <div className="chips">
        {items.map((t, i) => (
          <span key={i} className="chip">
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}
