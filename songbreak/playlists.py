"""Story-arc playlists: order songs so the playlist itself tells a story.

1. Each song gets an *affect* reading, intensity (0..1) and valence (−1 sad/dark .. +1 hopeful/bright),
   from the local LLM reading its story summary, themes and mood, or from audio heuristics without
   Ollama. Cached per song in affect.json, so each song is only scored once.
2. An arc template gives target intensity/valence curves over the playlist. A local search finds
   the order that follows them, with a small bonus for smooth transitions (tempo/key compatibility
   from the automixer).
3. The LLM writes a title, a synopsis and a "chapter" line per song for the final order.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import requests

from .mix import compatibility

# Each arc: target curves as (x, value) points over playlist position x in 0..1, plus named beats
# as (x_end, name). Intensity 0..1, valence −1..1.
ARCS = {
    "rise_resolve": {
        "name": "Rise & Resolve",
        "description": "Classic story shape: set the scene, build tension, peak, then land somewhere hopeful.",
        "intensity": [(0, 0.3), (0.35, 0.55), (0.7, 0.95), (0.85, 0.6), (1, 0.35)],
        "valence": [(0, 0.0), (0.5, -0.3), (0.7, -0.4), (1, 0.5)],
        "beats": [(0.2, "exposition"), (0.6, "rising action"), (0.78, "climax"), (0.9, "falling action"), (1, "resolution")],
    },
    "slow_burn": {
        "name": "Slow Burn",
        "description": "Starts quiet and keeps climbing; every track hits harder than the last.",
        "intensity": [(0, 0.15), (1, 1.0)],
        "valence": [(0, -0.1), (1, 0.3)],
        "beats": [(0.25, "spark"), (0.6, "build"), (0.85, "surge"), (1, "peak")],
    },
    "wind_down": {
        "name": "Wind Down",
        "description": "Opens at full energy and settles gently, for the end of the night.",
        "intensity": [(0, 0.9), (1, 0.15)],
        "valence": [(0, 0.2), (1, 0.3)],
        "beats": [(0.25, "high"), (0.6, "easing"), (0.85, "drift"), (1, "rest")],
    },
    "redemption": {
        "name": "Redemption",
        "description": "From darkness to light: the struggle songs first, the healing songs last.",
        "intensity": [(0, 0.5), (0.6, 0.75), (1, 0.45)],
        "valence": [(0, -0.8), (0.5, -0.3), (1, 0.8)],
        "beats": [(0.3, "the dark"), (0.6, "the struggle"), (0.8, "turning point"), (1, "the light")],
    },
    "tragedy": {
        "name": "Tragedy",
        "description": "Light to dark: it starts with hope and ends in heartbreak.",
        "intensity": [(0, 0.4), (0.7, 0.9), (1, 0.4)],
        "valence": [(0, 0.6), (0.5, 0.1), (1, -0.8)],
        "beats": [(0.3, "innocence"), (0.6, "cracks"), (0.8, "the fall"), (1, "aftermath")],
    },
}

_lock = threading.Lock()


def _curve(points: list[tuple[float, float]], x: float) -> float:
    xs, ys = zip(*points)
    return float(np.interp(x, xs, ys))


def _beat_slots(arc: dict, n: int) -> list[str]:
    """Share n slots across the beats in proportion to their width, at least one each when n allows,
    so a short playlist still gets its climax instead of skipping a narrow beat."""
    ends = [x for x, _ in arc["beats"]]
    widths = np.diff([0.0] + ends)
    if n < len(widths):
        return [next(name for x_end, name in arc["beats"] if i / max(n - 1, 1) <= x_end + 1e-9) for i in range(n)]
    counts = np.ones(len(widths), int)
    share = widths / widths.sum() * (n - len(widths))
    counts += np.floor(share).astype(int)
    for k in np.argsort(-(share - np.floor(share)))[: n - counts.sum()]:  # largest remainders
        counts[k] += 1
    return [name for (_, name), c in zip(arc["beats"], counts) for _ in range(c)]


def arc_targets(arc_id: str, n: int) -> list[dict]:
    arc = ARCS[arc_id]
    xs = [i / (n - 1) if n > 1 else 0.5 for i in range(n)]
    beats = _beat_slots(arc, n)
    return [{"x": x, "intensity": _curve(arc["intensity"], x), "valence": _curve(arc["valence"], x), "beat": b}
            for x, b in zip(xs, beats)]


def arc_catalog() -> list[dict]:
    return [{"id": k, "name": a["name"], "description": a["description"],
             "curve": [{"x": round(x, 3), "intensity": round(_curve(a["intensity"], x), 3),
                        "valence": round(_curve(a["valence"], x), 3)} for x in np.linspace(0, 1, 25)]}
            for k, a in ARCS.items()]


# ---------- affect ----------

def _heuristic_affect(a: dict) -> dict:
    """Without an LLM: tempo, drums and the classifier's mood tags for intensity; mood tags for valence."""
    st = a.get("structure") or {}
    tags = (a.get("instruments") or {}).get("song") or {}
    moods = {m: v for m, v in tags.get("mood") or []}
    tempo = st.get("tempo_bpm") or 110
    tempo = tempo / 2 if tempo > 160 else tempo  # double-time readings
    drums = (st.get("stem_share_pct") or {}).get("drums", 30) / 100
    intensity = 0.45 * np.clip((tempo - 70) / 80, 0, 1) + 0.35 * np.clip(drums / 0.5, 0, 1) \
        + 0.2 * min(1, 4 * (moods.get("Exciting music", 0) + moods.get("Angry music", 0)))
    valence = 3 * (moods.get("Happy music", 0) + moods.get("Funny music", 0) * 0.5
                   - moods.get("Sad music", 0) - moods.get("Scary music", 0) - moods.get("Angry music", 0) * 0.5)
    return {"intensity": round(float(np.clip(intensity, 0, 1)), 3), "valence": round(float(np.clip(valence, -1, 1)), 3),
            "why": "estimated from tempo, drums and mood tags", "source": "heuristic"}


def _llm_affect(items: list[tuple[str, dict]], model: str, host: str) -> dict[str, dict]:
    lines = []
    for i, (_, a) in enumerate(items):
        llm = (a.get("story") or {}).get("llm") or {}
        st = a.get("structure") or {}
        moods = [m for m, _ in ((a.get("instruments") or {}).get("song") or {}).get("mood") or []]
        lines.append(f"[{i}] {a.get('title')} | {st.get('tempo_bpm', '?')} BPM, {st.get('key', '?')} | "
                     f"summary: {llm.get('one_line_summary') or 'n/a'} | themes: {', '.join(llm.get('themes') or [])} | "
                     f"resolution: {llm.get('resolution') or 'n/a'} | audio mood tags: {', '.join(moods) or 'none'} | "
                     f"energy arc: {((a.get('story') or {}).get('heuristics') or {}).get('energy_arc', 'n/a')}")
    prompt = ("Rate each song's overall feel for sequencing a story playlist.\n"
              "intensity: 0 = calm, sparse, gentle .. 1 = intense, driving, cathartic.\n"
              "valence: -1 = dark, despairing .. 0 = bittersweet/ambivalent .. 1 = hopeful, joyful.\n"
              "Use the whole range across these songs. Reply with ONLY JSON: "
              '{"songs": [{"index": 0, "intensity": 0.5, "valence": 0.0, "why": "max 12 words"}]}\n\n' + "\n".join(lines))
    r = requests.post(f"{host}/api/chat", timeout=600, json={
        "model": model, "stream": False, "format": "json", "think": False, "keep_alive": "60s",
        "options": {"temperature": 0.2, "num_ctx": 8192},
        "messages": [{"role": "user", "content": prompt}],
    })
    r.raise_for_status()
    text = r.json()["message"]["content"]
    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    out = {}
    for row in data.get("songs", []):
        i = row.get("index")
        if isinstance(i, int) and 0 <= i < len(items):
            out[items[i][0]] = {"intensity": round(float(np.clip(row.get("intensity", 0.5), 0, 1)), 3),
                                "valence": round(float(np.clip(row.get("valence", 0), -1, 1)), 3),
                                "why": str(row.get("why", ""))[:120], "source": "llm", "model": model}
    return out


def affects(library: Path, ids: list[str], analyses: dict[str, dict], model: str | None, host: str,
            progress=None) -> dict[str, dict]:
    """Affect per song, from cache where possible. LLM-scored in batches of 8."""
    progress = progress or (lambda f, m: None)
    out, todo = {}, []
    for sid in ids:
        cache = library / sid / "affect.json"
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
        # Re-score heuristic guesses once an LLM is available.
        if cached and not (model and cached.get("source") == "heuristic"):
            out[sid] = cached
        else:
            todo.append(sid)
    for b in range(0, len(todo), 8):
        batch = todo[b:b + 8]
        progress(0.1 + 0.5 * b / max(len(todo), 1), f"Reading the mood of {len(todo)} song(s)")
        scored = {}
        if model:
            try:
                scored = _llm_affect([(s, analyses[s]) for s in batch], model, host)
            except (requests.RequestException, ValueError, KeyError):
                scored = {}
        for sid in batch:
            out[sid] = scored.get(sid) or _heuristic_affect(analyses[sid])
            (library / sid / "affect.json").write_text(json.dumps(out[sid], indent=1), encoding="utf-8")
    return out


# ---------- ordering ----------

def order_songs(ids: list[str], aff: dict[str, dict], analyses: dict[str, dict], arc_id: str,
                seed: int = 0) -> tuple[list[str], float]:
    """Order minimising distance to the arc's curves, plus a small penalty for rough transitions."""
    n = len(ids)
    if n < 2:
        return list(ids), 0.0
    targets = arc_targets(arc_id, n)
    flow = {(a, b): 1 - compatibility(analyses[a], analyses[b])["score"] for a in ids for b in ids if a != b}

    def fit(sid: str, pos: int) -> float:
        t = targets[pos]
        return (aff[sid]["intensity"] - t["intensity"]) ** 2 + 0.6 * ((aff[sid]["valence"] - t["valence"]) / 2) ** 2

    def cost(order: list[str]) -> float:
        return sum(fit(s, i) for i, s in enumerate(order)) + 0.08 * sum(flow[(a, b)] for a, b in zip(order, order[1:]))

    rng = random.Random(seed)
    best, best_cost = None, float("inf")
    for restart in range(12):
        order = list(ids)
        if restart == 0:  # greedy start: fill each slot with the best remaining fit
            order, left = [], set(ids)
            for pos in range(n):
                s = min(left, key=lambda s: fit(s, pos))
                order.append(s)
                left.remove(s)
        else:
            rng.shuffle(order)
        c, improved = cost(order), True
        while improved:  # pairwise-swap local search
            improved = False
            for i in range(n - 1):
                for j in range(i + 1, n):
                    order[i], order[j] = order[j], order[i]
                    c2 = cost(order)
                    if c2 < c - 1e-9:
                        c, improved = c2, True
                    else:
                        order[i], order[j] = order[j], order[i]
        if c < best_cost:
            best, best_cost = list(order), c
    return best, best_cost


# ---------- narrative ----------

def _llm_narrative(arc: dict, rows: list[dict], model: str, host: str) -> dict | None:
    lines = [f"[{i}] beat: {r['beat']} | {r['title']} | {r['summary'] or 'n/a'} | themes: {', '.join(r['themes'])}"
             for i, r in enumerate(rows)]
    prompt = (f"These songs are sequenced as a story playlist following the arc \"{arc['name']}\" "
              f"({arc['description']}). Treat the playlist as one story told across the songs.\n\n"
              + "\n".join(lines) +
              "\n\nReply with ONLY JSON: {\"title\": \"2-5 word playlist title\", "
              "\"synopsis\": \"2-3 sentences: the story this playlist tells\", "
              "\"chapters\": [{\"index\": 0, \"chapter\": \"one sentence: what this song does in the story\"}]}")
    try:
        r = requests.post(f"{host}/api/chat", timeout=600, json={
            "model": model, "stream": False, "format": "json", "think": False, "keep_alive": 0,
            "options": {"temperature": 0.6, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        text = r.json()["message"]["content"]
        return json.loads(text[text.find("{"):text.rfind("}") + 1])
    except (requests.RequestException, ValueError, KeyError):
        return None


def build(library: Path, ids: list[str], arc_id: str, model: str | None, host: str, name: str | None = None,
          progress=None) -> dict:
    progress = progress or (lambda f, m: None)
    if arc_id not in ARCS:
        raise ValueError(f"unknown arc: {arc_id}")
    analyses = {}
    for sid in ids:
        f = library / sid / "analysis.json"
        if f.exists():
            a = json.loads(f.read_text(encoding="utf-8"))
            if a.get("kind") != "mashup":
                analyses[sid] = a
    ids = list(analyses)
    if len(ids) < 2:
        raise ValueError("a story playlist needs at least 2 analysed songs (mashups aren't included)")

    aff = affects(library, ids, analyses, model, host, progress)
    progress(0.65, "Finding the order that tells the story")
    order, _ = order_songs(ids, aff, analyses, arc_id)
    targets = arc_targets(arc_id, len(order))
    rows = []
    for sid, t in zip(order, targets):
        llm = (analyses[sid].get("story") or {}).get("llm") or {}
        rows.append({"id": sid, "title": analyses[sid].get("title", sid), "beat": t["beat"],
                     "target": {"intensity": round(t["intensity"], 3), "valence": round(t["valence"], 3)},
                     "affect": aff[sid], "summary": llm.get("one_line_summary"), "themes": llm.get("themes") or []})
    for a, b in zip(rows, rows[1:]):
        c = compatibility(analyses[a["id"]], analyses[b["id"]])
        a["next"] = {"score": c["score"], "tempo": [c["tempo"]["from"], c["tempo"]["to"]], "camelot": c["key"]["camelot"]}

    progress(0.75, "Writing the playlist's story")
    arc = ARCS[arc_id]
    story = _llm_narrative(arc, rows, model, host) if model else None
    chapters = {c.get("index"): c.get("chapter") for c in (story or {}).get("chapters", []) if isinstance(c, dict)}
    for i, r in enumerate(rows):
        r["chapter"] = chapters.get(i) or r["summary"] or ""
    progress(1.0, "Done")
    return {
        "id": uuid.uuid4().hex[:10],
        "name": name or (story or {}).get("title") or f"{arc['name']} mix",
        "arc": arc_id,
        "arc_name": arc["name"],
        "synopsis": (story or {}).get("synopsis") or arc["description"],
        "songs": rows,
        "written_by": model if story else None,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ---------- storage ----------

class PlaylistStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "playlists.json"

    def load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("playlists", [])
        except (OSError, ValueError):
            return []

    def _save(self, playlists: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"playlists": playlists}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def put(self, playlist: dict) -> dict:
        with _lock:
            items = [p for p in self.load() if p["id"] != playlist["id"]]
            items.insert(0, playlist)
            self._save(items)
        return playlist

    def rename(self, pid: str, name: str) -> dict:
        with _lock:
            items = self.load()
            for p in items:
                if p["id"] == pid:
                    p["name"] = re.sub(r"\s+", " ", name).strip()[:80] or p["name"]
                    self._save(items)
                    return p
        raise KeyError(pid)

    def delete(self, pid: str) -> None:
        with _lock:
            self._save([p for p in self.load() if p["id"] != pid])

    def forget_song(self, song_id: str) -> None:
        """A deleted song drops out of every playlist it was in."""
        with _lock:
            items = self.load()
            for p in items:
                p["songs"] = [s for s in p["songs"] if s["id"] != song_id]
            self._save(items)
