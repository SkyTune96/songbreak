"""Library folders, with names suggested from each song's analysis.

Folders live in `library.json` at the library root, so a song's own analysis is never rewritten
to file it. Each song is in at most one folder; anything unassigned shows as "Unfiled".

Naming: the local LLM (Ollama) reads the songs' story summaries and themes and proposes a short
folder name; without Ollama, the most-shared story theme (or genre) is used. Either way the user
can rename it.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from collections import Counter
from pathlib import Path

import requests

_STOP = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "its", "is", "or", "as", "music"}
_lock = threading.Lock()


class FolderStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "library.json"

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"folders": data.get("folders", []), "assignments": data.get("assignments", {})}

    def save(self, data: dict) -> dict:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic, so a crash never leaves a half-written library.json
        return data

    def create(self, name: str, songs: list[str] | None = None) -> dict:
        with _lock:
            data = self.load()
            folder = {"id": uuid.uuid4().hex[:10], "name": name.strip()[:60] or "New folder"}
            data["folders"].append(folder)
            for s in songs or []:
                data["assignments"][s] = folder["id"]
            self.save(data)
            return folder

    def rename(self, folder_id: str, name: str) -> dict:
        with _lock:
            data = self.load()
            for f in data["folders"]:
                if f["id"] == folder_id:
                    f["name"] = name.strip()[:60] or f["name"]
                    self.save(data)
                    return f
            raise KeyError(folder_id)

    def delete(self, folder_id: str) -> None:
        """Remove the folder; its songs become unfiled (nothing on disk is deleted)."""
        with _lock:
            data = self.load()
            data["folders"] = [f for f in data["folders"] if f["id"] != folder_id]
            data["assignments"] = {s: f for s, f in data["assignments"].items() if f != folder_id}
            self.save(data)

    def assign(self, song_id: str, folder_id: str | None) -> None:
        with _lock:
            data = self.load()
            if folder_id is None:
                data["assignments"].pop(song_id, None)
            elif any(f["id"] == folder_id for f in data["folders"]):
                data["assignments"][song_id] = folder_id
            else:
                raise KeyError(folder_id)
            self.save(data)

    def forget(self, song_id: str) -> None:
        self.assign(song_id, None)


# ---------- naming ----------

def _profile(analysis: dict) -> dict:
    story = analysis.get("story") or {}
    llm = story.get("llm") or {}
    tags = (analysis.get("instruments") or {}).get("song") or {}
    return {
        "title": analysis.get("title", ""),
        "summary": llm.get("one_line_summary") or "",
        "themes": [t.lower() for t in llm.get("themes") or []],
        "genre": [g for g, _ in (tags.get("genre") or [])[:2]],
        "mood": [m for m, _ in (tags.get("mood") or [])[:2]],
        "kind": analysis.get("kind", "song"),
    }


def _theme_words(p: dict) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", " ".join(p["themes"])) if w not in _STOP and len(w) > 2}


def heuristic_name(profiles: list[dict]) -> str:
    if profiles and all(p["kind"] == "mashup" for p in profiles):
        return "Mashups"
    themes = Counter(t for p in profiles for t in p["themes"])
    words = Counter(w for p in profiles for w in _theme_words(p))
    if themes and themes.most_common(1)[0][1] > 1:
        return themes.most_common(1)[0][0].title()
    if words:
        top = [w for w, _ in words.most_common(2)]
        return " & ".join(w.title() for w in top)
    genres = Counter(g for p in profiles for g in p["genre"])
    return genres.most_common(1)[0][0] if genres else "New folder"


def llm_name(profiles: list[dict], model: str, host: str, avoid: list[str] | None = None) -> str | None:
    lines = [f"- {p['title']}: {p['summary'] or 'no summary'} (themes: {', '.join(p['themes']) or 'none'}; "
             f"genre: {', '.join(p['genre']) or 'unknown'})" for p in profiles[:12]]
    prompt = ("Name a music library folder holding these songs. Reply with ONLY the name: 1 to 4 words, "
              "evocative but clear (like a playlist title), no quotes, no emoji.\n\n" + "\n".join(lines))
    if avoid:
        # Without this, separately named folders all come back as "Echoes of ...".
        prompt += "\n\nOther folders are already called: " + "; ".join(avoid) + ". Use different wording."
    try:
        r = requests.post(f"{host}/api/chat", timeout=120, json={
            "model": model, "stream": False, "think": False, "keep_alive": "30s",
            "options": {"temperature": 0.7, "num_predict": 16},
            "messages": [{"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        name = r.json()["message"]["content"].strip().splitlines()[0]
        name = re.sub(r"^[\"'*#\s]+|[\"'*.\s]+$", "", name)
        return name[:60] if 0 < len(name.split()) <= 6 else None
    except (requests.RequestException, KeyError, ValueError, IndexError):
        return None


def suggest_name(analyses: list[dict], model: str | None, host: str, avoid: list[str] | None = None) -> dict:
    profiles = [_profile(a) for a in analyses]
    if profiles and all(p["kind"] == "mashup" for p in profiles):
        return {"name": "Mashups", "source": "kind"}
    if model:
        name = llm_name(profiles, model, host, avoid)
        if name:
            return {"name": name, "source": "llm"}
    return {"name": heuristic_name(profiles), "source": "themes"}


# ---------- auto-organize ----------

def auto_groups(analyses: dict[str, dict]) -> list[list[str]]:
    """Group songs that share story themes (or, failing that, a genre). Mashups get their own group.

    Each song joins the theme it shares with the most other songs, so groups stay readable.
    """
    profiles = {sid: _profile(a) for sid, a in analyses.items()}
    mashups = [s for s, p in profiles.items() if p["kind"] == "mashup"]
    songs = {s: p for s, p in profiles.items() if p["kind"] != "mashup"}
    counts = Counter(w for p in songs.values() for w in _theme_words(p))
    genre_counts = Counter(g for p in songs.values() for g in p["genre"][:1])
    groups: dict[str, list[str]] = {}
    for sid, p in songs.items():
        shared = [w for w in _theme_words(p) if counts[w] > 1]
        if shared:
            key = "t:" + max(shared, key=lambda w: (counts[w], w))
        elif p["genre"] and genre_counts[p["genre"][0]] > 1:
            key = "g:" + p["genre"][0]
        else:
            key = "misc"
        groups.setdefault(key, []).append(sid)
    out = [g for k, g in groups.items() if k != "misc" and len(g) > 1]
    # A song left alone still joins the group it shares the most theme words with, if any.
    leftovers = []
    for s in (s for k, g in groups.items() if k == "misc" or len(g) == 1 for s in g):
        words = _theme_words(songs[s])
        overlap = [(len(words & set().union(*(_theme_words(songs[m]) for m in g))), i) for i, g in enumerate(out)]
        best = max(overlap, default=(0, -1))
        if best[0] > 0:
            out[best[1]].append(s)
        else:
            leftovers.append(s)
    if len(leftovers) > 1:
        out.append(leftovers)
    if mashups:
        out.append(mashups)
    return out
