"""A yt-dlp stand-in that replays recorded calls, or records real ones.

`YouTubeFetcher` runs yt-dlp as a subprocess from `yt_dlp_cmd`. The `ytdlp_replay`
fixture in conftest.py points that command at this file, so the fetcher's real code
path runs unchanged against what yt-dlp really printed and wrote.

One recording per test, `<RECORDINGS_DIR>/<test module>/<test name>.json`, holding an
ordered list of `{args, returncode, stdout, stderr, files}`; the Nth call the test makes
is answered from entry N. Replay never starts yt-dlp: it compares the call's arguments to
the recorded ones, writes the recorded subtitle `files` into the requested output
directory and returns the recorded output. The only differences it tolerates are a
YYYYMMDD date (the fetcher's date filter moves every day) and the per-call temporary
output directory. Anything else, a missing recording, or a call beyond the recorded ones
is refused: exit code `REFUSED_EXIT`, the reason on stderr, and the reason appended to the
state directory's `failures.txt`, which the fixture turns into a test failure at teardown
so a caller that swallows the non-zero exit cannot hide it.

Record mode (`PRISMIS_RECORD_YTDLP=1`) runs the real `python -m yt_dlp` with the same
arguments and appends what it did to the recording.

Run as a script it is the command the fetcher executes; imported, it is the library the
fixture and the unit tests use.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RECORD_YTDLP_ENV = "PRISMIS_RECORD_YTDLP"
RECORDING_ENV = "PRISMIS_YTDLP_RECORDING"
STATE_ENV = "PRISMIS_YTDLP_STATE"
MODE_ENV = "PRISMIS_YTDLP_MODE"
COMMAND_ENV = "PRISMIS_YTDLP_RECORD_COMMAND"
REFUSED_EXIT = 97

DATE_TOKEN = "<date>"
TMP_TOKEN = "<tmp>"
_DATE = re.compile(r"(?<![\w-])(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?![\w-])")


def normalize_args(args: list[str]) -> list[str]:
    """`args` with every YYYYMMDD date and the per-call output directory tokenized."""
    out: list[str] = []
    previous = ""
    for arg in args:
        if previous == "--output":
            out.append(f"{TMP_TOKEN}/{Path(arg).name}")
        else:
            out.append(_DATE.sub(DATE_TOKEN, arg))
        previous = arg
    return out


def output_dir(args: list[str]) -> Path | None:
    """The directory of the call's `--output` template, or None when it has none."""
    for i, arg in enumerate(args[:-1]):
        if arg == "--output":
            return Path(args[i + 1]).parent
    return None


def _refuse(state: Path, message: str) -> int:
    full = f"{message}\nRe-record with: {os.environ.get(COMMAND_ENV, '<command unknown>')}"
    with (state / "failures.txt").open("a") as f:
        f.write(full + "\n---\n")
    sys.stderr.write(full + "\n")
    return REFUSED_EXIT


def _next_call_index(state: Path) -> int:
    counter = state / "calls"
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    return index


def _record(args: list[str], recording: Path) -> int:
    with tempfile.TemporaryDirectory() as scratch:
        real_args = list(args)
        target = output_dir(args)
        if target is not None:
            # Record into a directory this call owns, then keep what yt-dlp wrote.
            for i, arg in enumerate(real_args[:-1]):
                if arg == "--output":
                    real_args[i + 1] = str(Path(scratch) / Path(args[i + 1]).name)
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", *real_args],
            capture_output=True,
            text=True,
            timeout=180,
        )
        files = {
            p.name: p.read_text(encoding="utf-8")
            for p in sorted(Path(scratch).iterdir())
            if p.is_file()
        }
    entries: list[dict[str, Any]] = (
        json.loads(recording.read_text()) if recording.exists() else []
    )
    entries.append(
        {
            "args": normalize_args(args),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "files": files,
        }
    )
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (target / name).write_text(text, encoding="utf-8")
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def _replay(args: list[str], recording: Path, state: Path) -> int:
    index = _next_call_index(state)
    if not recording.exists():
        return _refuse(state, f"No yt-dlp recording exists at {recording}.")
    entries: list[dict[str, Any]] = json.loads(recording.read_text())
    if index >= len(entries):
        return _refuse(
            state,
            f"yt-dlp call {index + 1} is beyond the {len(entries)} recorded in "
            f"{recording}.",
        )
    entry = entries[index]
    live = normalize_args(args)
    if live != entry["args"]:
        return _refuse(
            state,
            f"yt-dlp call {index + 1} differs from the one recorded in {recording}.\n"
            f"recorded: {entry['args']}\nlive:     {live}",
        )
    target = output_dir(args)
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
        for name, text in entry["files"].items():
            (target / name).write_text(text, encoding="utf-8")
    sys.stdout.write(entry["stdout"])
    sys.stderr.write(entry["stderr"])
    return int(entry["returncode"])


def main(argv: list[str]) -> int:
    recording = Path(os.environ[RECORDING_ENV])
    state = Path(os.environ[STATE_ENV])
    if os.environ.get(MODE_ENV) == "record":
        return _record(argv, recording)
    return _replay(argv, recording, state)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
