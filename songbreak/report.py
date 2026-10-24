"""Write the human-readable report.md next to analysis.json."""
from __future__ import annotations


def _t(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def markdown(a: dict) -> str:
    st, tags, lyr, story = a.get("structure"), a.get("instruments"), a.get("lyrics"), a.get("story", {})
    out = [f"# {a['title']}", ""]
    p = a["profile"]
    out.append(f"*Analysed on {p['gpu']} ({p['vram_gb']} GB VRAM, {p['ram_gb']} GB RAM), profile **{p['name']}**.*")
    out.append("")

    if st:
        out += ["## Overview", "",
                f"- **Length:** {_t(st['duration'])}  **Tempo:** ~{st['tempo_bpm']} BPM  **Key:** {st['key']}"]
        if tags:
            g = ", ".join(lb for lb, _ in tags["song"]["genre"][:3]) or "—"
            m = ", ".join(lb for lb, _ in tags["song"]["mood"][:3]) or "—"
            v = ", ".join(lb for lb, _ in tags["song"]["vocal_style"][:3]) or "—"
            out += [f"- **Genre:** {g}", f"- **Mood:** {m}", f"- **Vocals:** {v}"]
        h = story.get("heuristics", {})
        if h:
            out += [f"- **Form:** {h['song_form']} — `{h['form_letters']}`",
                    f"- **Energy arc:** {h['energy_arc']}; climax at {h['climax_section']} ({_t(h['climax_time'])})",
                    f"- **Point of view:** {h['point_of_view']}",
                    f"- **Lyric repetition:** {h['lyric_repetition_pct']}% of lines recur"]
        out.append("")

    if a.get("stems"):
        out += ["## Stems", "", "| Stem | Share of energy | File |", "|---|---|---|"]
        shares = st.get("stem_share_pct", {}) if st else {}
        for name, path in a["stems"].items():
            share = shares.get(name)
            note = " (likely bleed)" if share is not None and share < 1 else ""
            out.append(f"| {name} | {share if share is not None else '—'}%{note} | `{path}` |")
        out.append("")
    if tags and tags["song"]["instruments"]:
        out += ["**Instruments heard:** " + ", ".join(f"{lb} ({v:.0%})" for lb, v in tags["song"]["instruments"][:12]),
                ""]

    if st:
        out += ["## Sections", "", "| # | Section | Time | Energy | Stems | Instruments |", "|---|---|---|---|---|---|"]
        for i, s in enumerate(st["sections"]):
            inst = ", ".join(lb for lb, _ in tags["sections"][i]["instrument"][:4]) if tags else ""
            bar = "█" * round(s["energy"] * 8) or "·"
            out.append(f"| {s['letter']} | {s['label']} | {_t(s['start'])}–{_t(s['end'])} | {bar} | "
                       f"{', '.join(s['stems_active'])} | {inst} |")
        out.append("")

    llm = story.get("llm")
    if llm:
        out += ["## Story", ""]
        if llm.get("one_line_summary"):
            out += [f"> {llm['one_line_summary']}", ""]
        for key, label in [("song_form", "Form"), ("story_mode", "Mode"), ("narrator", "Narrator"),
                           ("addressee", "Addressee"), ("setting", "Setting"),
                           ("central_conflict", "Central conflict"), ("turning_point", "Turning point"),
                           ("resolution", "Resolution"), ("music_and_story", "Music & story")]:
            if llm.get(key):
                out.append(f"- **{label}:** {llm[key]}")
        for key, label in [("characters", "Characters"), ("themes", "Themes"), ("devices", "Devices")]:
            if llm.get(key):
                out.append(f"- **{label}:** " + "; ".join(map(str, llm[key])))
        if llm.get("arc"):
            out += ["", "| Section | Beat | What happens | Emotion |", "|---|---|---|---|"]
            for b in llm["arc"]:
                out.append(f"| {b.get('section', '')} | {b.get('beat', '')} | {b.get('what_happens', '')} | "
                           f"{b.get('emotion', '')} |")
        out.append("")
    elif story.get("note"):
        out += ["## Story", "", story["note"], ""]

    if lyr and lyr["lines"]:
        out += ["## Lyrics", ""]
        secs = st["sections"] if st else []
        current = None
        for ln in lyr["lines"]:
            mid = (ln["start"] + ln["end"]) / 2
            sec = next((s for s in secs if s["start"] <= mid < s["end"]), None)
            if sec and sec["label"] != current:
                current = sec["label"]
                out += ["", f"**[{current}]**", ""]
            out.append(f"`{_t(ln['start'])}` {ln['text']}  ")
        out.append("")

    if a.get("timings"):
        out += ["## Run", "", "| Stage | Seconds | Peak VRAM (GB) |", "|---|---|---|"]
        for stage, t in a["timings"].items():
            out.append(f"| {stage} | {t['seconds']} | {t['peak_vram_gb']} |")
    return "\n".join(out) + "\n"
