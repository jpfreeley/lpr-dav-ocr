"""Command-line entry point: `lpr <command> [options]`."""
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, config

COMMANDS = {
    "process": ("lpr_dav_ocr.pipeline", "find plates in a folder of .dav clips (resumable)"),
    "second-pass": ("lpr_dav_ocr.second_pass", "re-read saved frames un-rotated for a second opinion"),
    "review": ("lpr_dav_ocr.review.server", "start the local review page"),
    "vehicles": ("lpr_dav_ocr.vehicles", "local make/body/year/color by plate from NYC ticket data (import, lookup)"),
    "dedupe": ("lpr_dav_ocr.dedupe", "merge duplicate sightings of one plate (keeps the clearer frame)"),
    "seen": ("lpr_dav_ocr.seen", "every time one plate was seen (exact match)"),
    "score": ("lpr_dav_ocr.score", "score the OCR passes against your review labels"),
    "watch": ("lpr_dav_ocr.watch", "process new clips as they appear and alert on watchlist plates"),
    "watchlist": ("lpr_dav_ocr.watchlist", "add, remove or list watched plates"),
    "demo": ("lpr_dav_ocr.demo", "generate synthetic demo data (no camera, no real plates)"),
}
BUILTIN = {
    "setup": "download the OCR models once (the only step besides alerts that uses the network)",
    "config": "show | path | init  (effective configuration)",
    "where": "print the frames folder a day of clips is written to",
}


def usage():
    w = max(len(k) for k in (*COMMANDS, *BUILTIN))
    lines = [f"lpr {__version__}", "usage: lpr <command> [options]   (lpr <command> -h for options)", ""]
    lines += [f"  {k:<{w}}  {v[1]}" for k, v in COMMANDS.items()]
    lines += [f"  {k:<{w}}  {v}" for k, v in BUILTIN.items()]
    return "\n".join(lines)


def _config_cmd(args):
    sub = args[0] if args else "show"
    if sub == "path":
        print(config.config_path())
    elif sub == "show":
        print(json.dumps(config.load(), indent=2))
    elif sub == "init":
        dst = config.config_path()
        if dst.exists():
            print(f"already exists: {dst}")
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        example = Path(__file__).resolve().parents[2] / "config" / "config.example.toml"
        if example.is_file():
            shutil.copy(example, dst)
        else:
            dst.write_text(_dump_defaults())
        print(f"wrote {dst}; edit paths.camera_dir at least")
    else:
        print("usage: lpr config show | path | init")
        return 2
    return 0


def _dump_defaults():
    out = ["# lpr-dav-ocr configuration. See docs/configuration.md for every key.\n"]
    for section, vals in config.DEFAULTS.items():
        out.append(f"\n[{section}]")
        for k, v in vals.items():
            out.append(f"{k} = {json.dumps(v)}")
    return "\n".join(out) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-V", "--version", "version"):
        print(__version__)
        return 0
    if cmd == "config":
        return _config_cmd(rest)
    if cmd == "setup":
        os.environ["LPR_ALLOW_NET"] = "1"          # models are fetched now, then used offline
        from . import pipeline
        pipeline.main(["--warmup"])
        print("models cached under", pipeline.HOME / "models")
        return 0
    if cmd == "where":
        from . import pipeline
        if not rest:
            print("usage: lpr where <folder-of-clips>/<YYYY-MM-DD>")
            return 2
        p = Path(rest[0])
        print(pipeline.default_out(p / "x.dav", p.name))
        return 0
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}\n\n{usage()}")
        return 2
    return importlib.import_module(COMMANDS[cmd][0]).main(rest) or 0
