"""songbreak — break a song into stems, lyrics, structure, instruments and story.

    python -m songbreak hardware
    python -m songbreak analyze "song.mp3" [--stems 4|6] [--skip story] [--language en] ...
    python -m songbreak serve --library DIR          (the desktop app's backend)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
# Model downloads print progress bars to stderr; a pipe from the app can't render them.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def cmd_hardware(args) -> None:
    from .hardware import detect
    print(json.dumps(detect(args.profile).to_dict(), indent=2))


def cmd_analyze(args) -> None:
    from .pipeline import STAGES, Options, run

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"File not found: {src}")
    skip = set(args.skip.split(",")) if args.skip else set()
    unknown = skip - set(STAGES)
    if unknown:
        sys.exit(f"Unknown stage(s) for --skip: {', '.join(sorted(unknown))}")
    opts = Options(stems=args.stems, skip=skip, language=args.language, llm=args.llm,
                   llm_model=args.llm_model, ollama_host=args.ollama_host, force=args.force,
                   profile=args.profile)
    run(src, Path(args.out) if args.out else Path("analysis") / src.stem, opts)


def cmd_serve(args) -> None:
    from .server import serve
    serve(Path(args.library), port=args.port, parent_pid=args.parent_pid)


def main() -> None:
    ap = argparse.ArgumentParser(prog="songbreak", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="auto", choices=["auto", "high", "mid", "low", "cpu"],
                    help="override the hardware tier (default: detect)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hardware", help="show the detected hardware profile").set_defaults(fn=cmd_hardware)

    an = sub.add_parser("analyze", help="analyse one audio file")
    an.add_argument("input")
    an.add_argument("-o", "--out", help="output folder (default: analysis/<song name>)")
    an.add_argument("--stems", type=int, choices=[4, 6], default=6,
                    help="6 = vocals/drums/bass/guitar/piano/other; 4 = higher-quality vocals/drums/bass/other")
    an.add_argument("--skip", help="comma-separated stages to skip: stems,lyrics,structure,instruments,story")
    an.add_argument("--language", help="lyrics language code, e.g. en (default: auto-detect)")
    an.add_argument("--llm", default="ollama", choices=["ollama", "none"])
    an.add_argument("--llm-model", help="Ollama model tag (default depends on profile)")
    an.add_argument("--ollama-host", default="http://localhost:11434")
    an.add_argument("--force", action="store_true", help="redo stems and lyrics even if a previous run saved them")
    an.set_defaults(fn=cmd_analyze)

    sv = sub.add_parser("serve", help="run the local API for the desktop app")
    sv.add_argument("--library", required=True, help="folder that holds analysed songs")
    sv.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    sv.add_argument("--parent-pid", type=int, help="exit when this process exits")
    sv.set_defaults(fn=cmd_serve)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
