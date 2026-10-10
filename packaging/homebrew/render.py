"""Render the prismis Homebrew formulas for a release.

Two jobs, both mechanical so no formula field is ever hand-maintained:

* ``resources``/``check``: generate prismis-cli's ``resource`` blocks from ``cli/uv.lock``
  (the sdist url and hash of every package the client-only install needs on macOS), and
  verify the committed formula still carries exactly that.
* ``fill``: write a release tag's source-tarball ``url``, ``sha256`` and ``version`` into
  the three formulas, changing nothing else.

Usage:
    render.py check [--formula-dir D] [--lock L]
    render.py resources [--lock L]
    render.py fill TAG TARBALL [--formula-dir D]
"""

import argparse
import hashlib
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.markers import Marker

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_LOCK = REPO / "cli" / "uv.lock"

ROOT_PACKAGE = "prismis-cli"
FORMULAS = ("prismis-tui", "prismis-cli", "prismis-daemon")
CLI_FORMULA = "prismis-cli"

BEGIN = "# BEGIN RESOURCES"
END = "# END RESOURCES"

TAG_URL = "https://github.com/nickpending/prismis/archive/refs/tags/{tag}.tar.gz"

# The marker environment of the macOS host the formulas target, on the interpreter the
# formulas depend on (python@3.14).
MACOS_ENV = {
    "sys_platform": "darwin",
    "platform_system": "Darwin",
    "os_name": "posix",
    "platform_machine": "arm64",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "python_version": "3.14",
    "python_full_version": "3.14.0",
}


def _marker_applies(marker: str | None) -> bool:
    return marker is None or Marker(marker).evaluate(MACOS_ENV)


def _client_closure(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Packages reachable from the root's plain dependencies, markers evaluated for macOS.

    Optional dependencies (the ``local`` extra) and dependency groups are not followed,
    which is what keeps prismis-daemon and the dev tools out of the client install.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for p in packages:
        by_name.setdefault(p["name"], []).append(p)
    roots = by_name.get(ROOT_PACKAGE)
    if not roots:
        raise ValueError(f"{ROOT_PACKAGE} is not in the lock")

    seen: dict[str, dict[str, Any]] = {}
    queue = [roots[0]]
    while queue:
        pkg = queue.pop()
        for dep in pkg.get("dependencies", []):
            if not _marker_applies(dep.get("marker")):
                continue
            name = dep["name"]
            candidates = by_name.get(name, [])
            if "version" in dep:
                candidates = [c for c in candidates if c["version"] == dep["version"]]
            if len(candidates) != 1:
                raise ValueError(f"{name}: expected one locked package, found {len(candidates)}")
            if name not in seen:
                seen[name] = candidates[0]
                queue.append(candidates[0])
    return sorted(seen.values(), key=lambda p: p["name"])


def render_resources(lock_path: Path) -> str:
    """The ``resource`` blocks for every client-only dependency, from the lock's sdists."""
    data = tomllib.loads(lock_path.read_text())
    blocks = []
    for pkg in _client_closure(data["package"]):
        sdist = pkg.get("sdist")
        if not sdist or "url" not in sdist or "hash" not in sdist:
            raise ValueError(f"{pkg['name']}: the lock holds no sdist url and hash to pin")
        algo, _, digest = sdist["hash"].partition(":")
        if algo != "sha256":
            raise ValueError(f"{pkg['name']}: unsupported hash algorithm {algo}")
        blocks.append(
            f'  resource "{pkg["name"]}" do\n'
            f'    url "{sdist["url"]}"\n'
            f'    sha256 "{digest}"\n'
            "  end\n"
        )
    return "\n".join(blocks)


def _resources_span(text: str) -> tuple[int, int]:
    m = re.search(rf"^  {re.escape(BEGIN)}\n(.*?)^  {re.escape(END)}\n", text, re.S | re.M)
    if not m:
        raise ValueError(f"resource markers {BEGIN!r}/{END!r} missing from the formula")
    return m.start(1), m.end(1)


def committed_resources(formula: Path) -> str:
    text = formula.read_text()
    start, end = _resources_span(text)
    return text[start:end]


def write_resources(formula: Path, lock_path: Path) -> None:
    text = formula.read_text()
    start, end = _resources_span(text)
    formula.write_text(text[:start] + render_resources(lock_path) + text[end:])


def _sub_once(text: str, field: str, pattern: str, replacement: str, formula: str) -> str:
    new, n = re.subn(pattern, lambda _: replacement, text, flags=re.M)
    if n != 1:
        raise ValueError(f"{formula}: expected exactly one top-level {field} line, found {n}")
    return new


def fill_formulas(formula_dir: Path, tag: str, tarball: Path) -> None:
    """Write the tag's url, the tarball's sha256 and the tag's version into the formulas."""
    if not re.fullmatch(r"v?\d+(\.\d+)*[0-9A-Za-z.+-]*", tag):
        raise ValueError(f"not a release tag: {tag!r}")
    version = tag.removeprefix("v")
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    fields = (
        ("url", r'^  url ".*"$', f'  url "{TAG_URL.format(tag=tag)}"'),
        ("sha256", r'^  sha256 ".*"$', f'  sha256 "{sha}"'),
        ("version", r'^  version ".*"$', f'  version "{version}"'),
    )
    for name in FORMULAS:
        path = formula_dir / f"{name}.rb"
        text = path.read_text()
        for field, pattern, replacement in fields:
            text = _sub_once(text, field, pattern, replacement, name)
        path.write_text(text)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("check", "resources", "fill", "write-resources"):
        p = sub.add_parser(cmd)
        p.add_argument("--formula-dir", type=Path, default=HERE)
        p.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
        if cmd == "fill":
            p.add_argument("tag")
            p.add_argument("tarball", type=Path)
    args = parser.parse_args(argv)

    if args.command == "resources":
        sys.stdout.write(render_resources(args.lock))
        return 0
    if args.command == "write-resources":
        write_resources(args.formula_dir / f"{CLI_FORMULA}.rb", args.lock)
        return 0
    if args.command == "check":
        committed = committed_resources(args.formula_dir / f"{CLI_FORMULA}.rb")
        if committed != render_resources(args.lock):
            sys.stderr.write(
                f"{CLI_FORMULA}.rb resources differ from {args.lock}; "
                "run `render.py write-resources`\n"
            )
            return 1
        return 0
    fill_formulas(args.formula_dir, args.tag, args.tarball)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
