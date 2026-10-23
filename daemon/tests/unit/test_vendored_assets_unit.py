"""Unit test for the vendored browser scripts served from static/assets/.

INVARIANT: every file VENDORED.json names exists, hashes to its recorded sha256 and carries a
           banner naming its recorded version
BREAKS: a hand-edited, truncated or half-bumped vendored script ships as marked or
        DOMPurify, and the web view renders model output through code nobody reviewed
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import prismis_daemon

ASSETS = Path(prismis_daemon.__file__).parent / "static" / "assets"
MANIFEST_PATH = ASSETS / "VENDORED.json"
REQUIRED_FIELDS = {"package", "version", "tarball", "license", "sha256", "banner"}


def verify_vendored(assets: Path) -> list[str]:
    """Return one problem per file in `assets`/VENDORED.json that fails its record."""
    manifest = json.loads((assets / "VENDORED.json").read_text())
    problems: list[str] = []
    for name, record in manifest.items():
        path = assets / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != record["sha256"]:
            problems.append(f"{name}: sha256 {digest} != recorded {record['sha256']}")
        banner = f"{record['banner']}"
        if banner not in data[:400].decode("utf-8", errors="replace"):
            problems.append(f"{name}: banner {banner!r} not in the file header")
        if record["version"] not in banner:
            problems.append(
                f"{name}: banner {banner!r} does not name {record['version']}"
            )
    return problems


def test_manifest_records_both_scripts_completely() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    assert set(manifest) == {"marked.umd.js", "purify.min.js"}
    for name, record in manifest.items():
        assert REQUIRED_FIELDS <= set(record), name
    assert manifest["marked.umd.js"]["banner"] == "marked v18.0.14"
    assert manifest["purify.min.js"]["banner"] == "DOMPurify 3.4.16"


def test_committed_vendored_files_match_their_record() -> None:
    assert verify_vendored(ASSETS) == []


@pytest.fixture
def assets_copy(tmp_path: Path) -> Path:
    for source in ASSETS.iterdir():
        (tmp_path / source.name).write_bytes(source.read_bytes())
    return tmp_path


def test_missing_file_is_reported(assets_copy: Path) -> None:
    (assets_copy / "purify.min.js").unlink()
    assert verify_vendored(assets_copy) == ["purify.min.js: missing"]


def test_altered_file_is_reported_by_hash(assets_copy: Path) -> None:
    path = assets_copy / "marked.umd.js"
    path.write_bytes(path.read_bytes() + b"\n// tampered\n")
    problems = verify_vendored(assets_copy)
    assert len(problems) == 1
    assert problems[0].startswith("marked.umd.js: sha256 ")


def test_file_without_the_recorded_version_banner_is_reported(
    assets_copy: Path,
) -> None:
    path = assets_copy / "purify.min.js"
    body = path.read_bytes().replace(b"DOMPurify 3.4.16", b"DOMPurify 3.4.17")
    path.write_bytes(body)
    manifest = json.loads((assets_copy / "VENDORED.json").read_text())
    manifest["purify.min.js"]["sha256"] = hashlib.sha256(body).hexdigest()
    (assets_copy / "VENDORED.json").write_text(json.dumps(manifest))
    problems = verify_vendored(assets_copy)
    assert problems == [
        "purify.min.js: banner 'DOMPurify 3.4.16' not in the file header"
    ]
