"""The release logic the tag workflow runs: fill the three formulas and stage them in the tap.

Usage:
    release.py TAG TARBALL TAP_DIR [--repo-root R]

Refuses unless TAG (``vX.Y.Z``) matches the version in daemon/pyproject.toml and
cli/pyproject.toml and ``render.py check`` passes. The three formulas are filled on a
scratch copy and written to ``TAP_DIR/Formula`` only after every step has passed, so a
failure leaves TAP_DIR untouched.
"""

import argparse
import hashlib
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

import render

UNITS = ("daemon", "cli")


class ReleaseError(Exception):
    pass


def pyproject_version(repo: Path, unit: str) -> str:
    path = repo / unit / "pyproject.toml"
    return str(tomllib.loads(path.read_text())["project"]["version"])


def release(tag: str, tarball: Path, tap: Path, repo: Path) -> str:
    """Fill the formulas for ``tag`` into ``tap/Formula``; return the tarball's sha256."""
    if not tag.startswith("v"):
        raise ReleaseError(f"not a release tag (expected a leading v): {tag!r}")
    version = tag.removeprefix("v")
    for unit in UNITS:
        found = pyproject_version(repo, unit)
        if found != version:
            raise ReleaseError(
                f"tag {tag} wants version {version} but {unit}/pyproject.toml says {found}"
            )

    formula_dir = repo / "packaging" / "homebrew"
    lock = repo / "cli" / "uv.lock"
    if (
        render.main(["check", "--formula-dir", str(formula_dir), "--lock", str(lock)])
        != 0
    ):
        raise ReleaseError("render.py check failed")

    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name)
        for name in render.FORMULAS:
            shutil.copy(formula_dir / f"{name}.rb", scratch / f"{name}.rb")
        try:
            render.fill_formulas(scratch, tag, tarball)
        except ValueError as exc:
            raise ReleaseError(str(exc)) from exc
        target = tap / "Formula"
        target.mkdir(exist_ok=True)
        for name in render.FORMULAS:
            shutil.copy(scratch / f"{name}.rb", target / f"{name}.rb")
    return sha


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("tag")
    parser.add_argument("tarball", type=Path)
    parser.add_argument("tap", type=Path)
    parser.add_argument("--repo-root", type=Path, default=render.REPO)
    args = parser.parse_args(argv)
    try:
        sha = release(args.tag, args.tarball, args.tap, args.repo_root)
    except ReleaseError as exc:
        sys.stderr.write(f"release: {exc}\n")
        return 1
    sys.stdout.write(
        f"release: tag={args.tag} sha256={sha} formulas -> {args.tap / 'Formula'}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
