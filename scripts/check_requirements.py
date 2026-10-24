"""Fail if the engine imports a third-party package that requirements.txt doesn't list.

The desktop app builds its Python environment from requirements.txt, so a missing entry only
shows up on a fresh install. Run in CI:  python scripts/check_requirements.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Import name -> distribution name, where they differ. torch is installed separately (CUDA index).
DIST = {"imageio_ffmpeg": "imageio-ffmpeg", "soundfile": "soundfile", "yaml": "pyyaml", "python_stretch": "python-stretch"}
PROVIDED = {"torch", "torchaudio", "songbreak"}

listed = set()
for line in (ROOT / "requirements.txt").read_text().splitlines():
    line = line.split("#")[0].strip()
    if line:
        listed.add(re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0].lower())

imported = set()
for f in (ROOT / "songbreak").glob("*.py"):
    for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

third_party = {m for m in imported if m not in sys.stdlib_module_names and m not in PROVIDED}
missing = sorted(m for m in third_party if DIST.get(m, m).lower() not in listed)
if missing:
    sys.exit(f"requirements.txt is missing: {', '.join(missing)}")
print(f"ok: {len(third_party)} third-party imports all listed ({', '.join(sorted(third_party))})")
