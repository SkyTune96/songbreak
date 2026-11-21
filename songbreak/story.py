"""Stage 5: the song's story — form, point of view, narrative arc, themes.

Two layers:
  * heuristics that always run (form type, POV from pronouns, energy arc, climax);
  * an optional local LLM via Ollama that reads the timed lyrics + section map and
    writes the narrative breakdown. The prompt is always saved, so it can also be
    pasted into any chatbot.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import numpy as np
import requests

_FIRST = {"i", "me", "my", "mine", "myself", "i'm", "i've", "i'll", "i'd", "we", "us", "our", "ours"}
_SECOND = {"you", "your", "yours", "yourself", "you're", "you've", "you'll", "you'd", "ya"}
_THIRD = {"he", "she", "him", "her", "his", "hers", "they", "them", "their", "he's", "she's", "they're"}


def heuristics(structure: dict, lines: list[dict]) -> dict:
    words = [w.lower().strip(".,!?\"'()") for ln in lines for w in ln["text"].split()]
    c = Counter("first" if w in _FIRST else "second" if w in _SECOND else "third" if w in _THIRD else None
                for w in words)
    c.pop(None, None)
    pov = "unclear (few pronouns)"
    if sum(c.values()) >= 5:
        main = c.most_common(1)[0][0]
        pov = {"first": "first person (the singer is the narrator)",
               "second": "second person (sung *to* someone)",
               "third": "third person (telling someone else's story)"}[main]
        if main == "first" and c["second"] >= 0.4 * c["first"]:
            pov = "first person addressing a 'you' (direct address)"

    norm = [re.sub(r"[^a-z' ]", "", ln["text"].lower()).strip() for ln in lines]
    counts = Counter(n for n in norm if n)
    repeated = sum(v for v in counts.values() if v > 1) / max(len(norm), 1)

    secs = structure["sections"]
    labels = [re.sub(r" \d+$", "", s["label"]) for s in secs]
    letters = structure["form_letters"]
    if "Chorus" in labels:
        form = "Verse–Chorus" + (" with bridge" if "Bridge" in labels else "") + \
               (" and pre-chorus" if "Pre-Chorus" in labels else "")
    elif re.fullmatch(r"A+BA+", letters):
        form = "AABA (32-bar song form)"
    elif len(set(letters)) == 1:
        form = "Strophic (AAA — same music, new words each time)"
    else:
        form = "Through-composed / free form"

    energy = np.array([s["energy"] for s in secs])
    climax = int(np.argmax(energy + np.linspace(0, 0.05, len(energy))))  # ties go to the later section
    thirds = np.array_split(energy, 3) if len(energy) >= 3 else [energy] * 3
    a, b, z = (float(t.mean()) for t in thirds)
    if z > a + 0.15 and z >= b:
        arc = "builds — energy keeps rising to the end"
    elif b > a + 0.15 and b > z + 0.15:
        arc = "arch — rises to a peak in the middle, then winds down"
    elif z < a - 0.15:
        arc = "fades — starts strongest and recedes"
    else:
        arc = "plateau — energy stays roughly level, driven by repetition"

    return {
        "song_form": form,
        "form_letters": letters,
        "section_sequence": [s["label"] for s in secs],
        "point_of_view": pov,
        "pronoun_counts": dict(c),
        "lyric_repetition_pct": round(100 * repeated, 1),
        "energy_arc": arc,
        "climax_section": secs[climax]["label"] if secs else None,
        "climax_time": secs[climax]["start"] if secs else None,
    }


def build_prompt(title: str, structure: dict, tags: dict | None, lines: list[dict], heur: dict) -> str:
    def fmt(t: float) -> str:
        return f"{int(t // 60)}:{t % 60:04.1f}"

    out = [f"Song: {title}",
           f"Duration {fmt(structure['duration'])}, ~{structure['tempo_bpm']} BPM, key {structure['key']}"]
    if tags:
        g = ", ".join(lb for lb, _ in tags["song"]["genre"][:3]) or "unknown"
        m = ", ".join(lb for lb, _ in tags["song"]["mood"][:3]) or "unknown"
        out.append(f"Audio classifier genre guess: {g}. Mood guess: {m}.")
    out.append(f"Heuristic form: {heur['song_form']} ({heur['form_letters']}); energy arc: {heur['energy_arc']}.")
    out.append("\nSECTIONS (auto-detected; labels may be wrong — correct them using the lyrics):")
    for i, s in enumerate(structure["sections"]):
        inst = ""
        if tags:
            inst = ", ".join(lb for lb, _ in tags["sections"][i]["instrument"][:5])
        out.append(f"[{i}] {s['label']} (group {s['letter']}) {fmt(s['start'])}–{fmt(s['end'])} "
                   f"energy {s['energy']:.2f} | stems: {', '.join(s['stems_active']) or '-'}"
                   + (f" | instruments: {inst}" if inst else ""))
        sec_lines = [ln["text"] for ln in lines if s["start"] <= (ln["start"] + ln["end"]) / 2 < s["end"]]
        out.extend(f"      {t}" for t in sec_lines)
    out.append("""
Lyrics were machine-transcribed from an isolated vocal and may contain errors; infer intent.

Analyse this song as a piece of storytelling. Reply with ONLY a JSON object:
{
  "section_labels": [{"index": 0, "label": "Intro"}, ...],   // corrected, one per section above
  "song_form": "e.g. Verse–Chorus–Bridge (ABABCB)",
  "story_mode": "narrative | confessional | direct address | descriptive/scene | abstract/impressionistic",
  "narrator": "who is singing, as a character",
  "addressee": "who they are singing to, or null",
  "characters": ["..."],
  "setting": "place / time, or null",
  "central_conflict": "the tension or desire driving the song",
  "arc": [{"section": "Verse 1", "beat": "exposition | rising action | climax | falling action | resolution | refrain",
           "what_happens": "...", "emotion": "..."}],
  "turning_point": "where and how the song shifts",
  "resolution": "how it ends: resolved, unresolved, cyclical, ironic...",
  "themes": ["..."],
  "devices": ["imagery, metaphors, motifs, hooks worth noting"],
  "music_and_story": "how the arrangement and energy support or contrast the lyrics",
  "one_line_summary": "..."
}""")
    return "\n".join(out)


def ollama_catalog(host: str) -> list[dict] | None:
    """Installed Ollama models with size and family, or None if Ollama isn't running."""
    try:
        r = requests.get(f"{host}/api/tags", timeout=3)
        r.raise_for_status()
        return [{"name": m["name"], "size_gb": m.get("size", 0) / 1024**3,
                 "family": (m.get("details") or {}).get("family", "")} for m in r.json().get("models", [])]
    except requests.RequestException:
        return None


def ollama_models(host: str) -> list[str] | None:
    cat = ollama_catalog(host)
    return None if cat is None else [m["name"] for m in cat]


def _installed(name: str, want: str) -> bool:
    return name == want or name.split(":")[0] == want


def _is_chat(m: dict) -> bool:
    return "embed" not in m["name"].lower() and m["family"] not in ("bert", "nomic-bert")


def resolve_model(requested: str | None, default: str, host: str, vram_gb: float) -> tuple[str | None, str | None]:
    """The model to write stories with, and a note when it isn't the one asked for.

    Uses the requested model (Settings) or the hardware default if installed. Otherwise it falls back
    to the best installed chat model instead of giving up: the largest that fits in VRAM, else the
    smallest. Embedding-only models are never picked.
    """
    cat = ollama_catalog(host)
    if cat is None:
        return None, ("Ollama isn't running, so only the heuristic story analysis ran. "
                      f"Start Ollama (or install it and `ollama pull {default}`), or paste story_prompt.md into any chatbot.")
    for want in (requested, default):
        hit = next((m["name"] for m in cat if want and _installed(m["name"], want)), None)
        if hit:
            note = None if want == requested or not requested else f"`{requested}` isn't installed; used `{hit}`."
            return hit, note
    chats = [m for m in cat if _is_chat(m)]
    if not chats:
        return None, f"Ollama has no chat models installed. Run `ollama pull {default}`."
    budget = max(vram_gb - 1.5, 2.0)  # leave headroom for the context window
    # Stock models before custom ones of the same size: a custom model may carry its own persona prompt.
    stock = ("qwen", "gemma", "llama", "mistral", "phi", "deepseek", "granite", "olmo")
    fits = sorted((m for m in chats if m["size_gb"] <= budget),
                  key=lambda m: (not m["name"].lower().startswith(stock), -m["size_gb"]))
    pick = fits[0] if fits else min(chats, key=lambda m: m["size_gb"])
    return pick["name"], f"`{requested or default}` isn't installed, so `{pick['name']}` wrote this story."


def ask_ollama(prompt: str, model: str, host: str) -> dict:
    r = requests.post(f"{host}/api/chat", timeout=900, json={
        # keep_alive 0 unloads at once; Ollama's default 5 min would sit on 10 GB while the next song's Whisper loads.
        "model": model, "stream": False, "format": "json", "think": False, "keep_alive": 0,
        "options": {"num_ctx": 8192, "temperature": 0.3},
        "messages": [{"role": "system", "content": "You are a musicologist and literary critic. "
                                                   "You answer with strict JSON only."},
                     {"role": "user", "content": prompt}],
    })
    r.raise_for_status()
    text = r.json()["message"]["content"]
    return json.loads(text[text.find("{"):text.rfind("}") + 1])
