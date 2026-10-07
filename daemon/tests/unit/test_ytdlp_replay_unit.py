"""The record/replay mechanism behind `ytdlp_replay`.

Every test enters through `YouTubeFetcher`'s own seams (`_extract_transcript` and
`_discover_channel_videos`), the calls production makes, against the real stand-in script
the fetcher's `yt_dlp_cmd` points at. The recordings are written into a per-test temp
directory, so what a replay does is decided by the file the test wrote, not by the
committed ones.

Nothing here reaches YouTube. Where the real `python -m yt_dlp` would run, a fake
`yt_dlp` package first on PYTHONPATH runs instead and leaves a marker file: a marker
after a replay would mean the stand-in started yt-dlp, and its absence is what "replay
never runs yt-dlp" is measured by.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ytdlp_replay as replay_script
from conftest import YtdlpReplay, make_config
from prismis_daemon.fetchers.youtube import YouTubeFetcher

MODULE = "test_ytdlp_replay_unit"
VIDEO_URL = "https://www.youtube.com/watch?v=abc123XYZ_-"
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello recorded world\n"


@pytest.fixture
def ytdlp_recordings_dir(tmp_path: Path) -> Path:
    return tmp_path / "recordings"


@pytest.fixture
def fetcher(ytdlp_replay: YtdlpReplay) -> YouTubeFetcher:
    return ytdlp_replay.fetcher(config=make_config())


@pytest.fixture
def fake_yt_dlp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `yt_dlp` package that stands where the real one would, and marks that it ran.

    Returns the marker file's path. As a subtitle download it writes one VTT file into
    the `--output` directory; as a discovery call it prints one JSON line.
    """
    package = tmp_path / "fake_site" / "yt_dlp"
    package.mkdir(parents=True)
    marker = tmp_path / "yt_dlp_ran"
    package.joinpath("__init__.py").write_text("")
    package.joinpath("__main__.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "args = sys.argv[1:]\n"
        "if '--output' in args:\n"
        "    out = Path(args[args.index('--output') + 1]).parent\n"
        f"    (out / 'abc123XYZ_-.en-orig.vtt').write_text({VTT!r})\n"
        "else:\n"
        "    print(json.dumps({'id': 'v1', 'title': 'T', 'duration': 5,\n"
        "        'upload_date': '20260101', 'view_count': 3,\n"
        "        'webpage_url': 'https://www.youtube.com/watch?v=v1'}))\n"
        "sys.stderr.write('recorded warning\\n')\n"
    )
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            [str(tmp_path / "fake_site"), os.environ.get("PYTHONPATH", "")]
        ),
    )
    return marker


def _recording_path(ytdlp_replay: YtdlpReplay) -> Path:
    return ytdlp_replay.path


def _capture_transcript_args(fetcher: YouTubeFetcher, tmp_path: Path) -> list[str]:
    """The arguments `_extract_transcript` passes yt-dlp, read off a spy command."""
    spy = tmp_path / "spy.py"
    out = tmp_path / "spy_args.json"
    spy.write_text(
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(out)!r}, 'w'))\n"
    )
    real = fetcher.yt_dlp_cmd
    fetcher.yt_dlp_cmd = [sys.executable, str(spy)]
    try:
        fetcher._extract_transcript(VIDEO_URL)
    finally:
        fetcher.yt_dlp_cmd = real
    args: list[str] = json.loads(out.read_text())
    return args


def _write_recording(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries))


def _transcript_entry(args: list[str], **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "args": replay_script.normalize_args(args),
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "files": {"abc123XYZ_-.en-orig.vtt": VTT},
    }
    entry.update(overrides)
    return entry


def test_replay_returns_the_recorded_output_and_files_under_no_network(
    fetcher: YouTubeFetcher,
    ytdlp_replay: YtdlpReplay,
    fake_yt_dlp: Path,
    tmp_path: Path,
) -> None:
    args = _capture_transcript_args(fetcher, tmp_path)
    _write_recording(_recording_path(ytdlp_replay), [_transcript_entry(args)])

    result = fetcher._extract_transcript(VIDEO_URL)

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:1", "replay must run under no_network"
    assert result.outcome == "extracted"
    assert result.text == "Hello recorded world"
    assert not fake_yt_dlp.exists(), "replay must never start yt-dlp"
    assert ytdlp_replay.failures == []


def test_missing_recording_is_refused_naming_file_and_record_command(
    fetcher: YouTubeFetcher,
    ytdlp_replay: YtdlpReplay,
    fake_yt_dlp: Path,
    tmp_path: Path,
) -> None:
    result = fetcher._extract_transcript(VIDEO_URL)

    assert result.outcome == "fetch_failed", "the missing recording must not pass as a transcript"
    assert len(ytdlp_replay.failures) == 1
    assert str(ytdlp_replay.path) in ytdlp_replay.failures[0]
    assert "PRISMIS_RECORD_YTDLP=1" in ytdlp_replay.failures[0]
    assert not fake_yt_dlp.exists(), "a missing recording must not fall back to yt-dlp"
    with pytest.raises(pytest.fail.Exception) as raised:
        ytdlp_replay.finalize()
    assert str(ytdlp_replay.path) in str(raised.value)
    ytdlp_replay.clear_failures()
    # The refusal is asserted on and cleared; leave a recording the one call has used up
    # so the fixture's own teardown has nothing left to object to.
    _write_recording(_recording_path(ytdlp_replay), [])


def test_arguments_that_differ_in_more_than_a_date_or_temp_path_are_refused(
    fetcher: YouTubeFetcher,
    ytdlp_replay: YtdlpReplay,
    fake_yt_dlp: Path,
    tmp_path: Path,
) -> None:
    args = _capture_transcript_args(fetcher, tmp_path)
    changed = [a if a != "en-orig,en,en-US,en-GB" else "fr" for a in args]
    assert changed != args
    _write_recording(_recording_path(ytdlp_replay), [_transcript_entry(changed)])

    result = fetcher._extract_transcript(VIDEO_URL)

    assert result.outcome == "fetch_failed", "a mismatched call must not pass as a transcript"
    assert len(ytdlp_replay.failures) == 1
    assert str(ytdlp_replay.path) in ytdlp_replay.failures[0]
    assert "PRISMIS_RECORD_YTDLP=1" in ytdlp_replay.failures[0]
    assert "fr" in ytdlp_replay.failures[0] and "en-orig" in ytdlp_replay.failures[0]
    assert not fake_yt_dlp.exists()
    ytdlp_replay.clear_failures()


def test_a_date_and_a_different_temp_directory_are_tolerated(
    fetcher: YouTubeFetcher,
    ytdlp_replay: YtdlpReplay,
    fake_yt_dlp: Path,
    tmp_path: Path,
) -> None:
    recorded = [
        "--simulate",
        "--break-match-filters",
        "upload_date>=20200102",
        "--output",
        "/some/other/dir/%(id)s.%(ext)s",
        "--",
        "https://www.youtube.com/@LexClips",
    ]
    live = [
        "--simulate",
        "--break-match-filters",
        "upload_date>=20261007",
        "--output",
        str(tmp_path / "live" / "%(id)s.%(ext)s"),
        "--",
        "https://www.youtube.com/@LexClips",
    ]
    _write_recording(
        _recording_path(ytdlp_replay),
        [
            {
                "args": replay_script.normalize_args(recorded),
                "returncode": 0,
                "stdout": "out\n",
                "stderr": "",
                "files": {},
            }
        ],
    )

    proc = subprocess.run(
        [*fetcher.yt_dlp_cmd, *live], capture_output=True, text=True, check=False
    )

    assert proc.returncode == 0
    assert proc.stdout == "out\n"
    assert ytdlp_replay.failures == []
    assert not fake_yt_dlp.exists()


def _discovery_entry(args: list[str]) -> dict[str, object]:
    return {
        "args": replay_script.normalize_args(args),
        "returncode": 0,
        "stdout": "first\n",
        "stderr": "",
        "files": {},
    }


def _run(ytdlp_replay: YtdlpReplay, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ytdlp_replay.cmd, *args], capture_output=True, text=True, check=False
    )


def test_a_number_that_is_not_a_date_is_not_ignored(
    ytdlp_replay: YtdlpReplay, fake_yt_dlp: Path
) -> None:
    _write_recording(
        _recording_path(ytdlp_replay),
        [_discovery_entry(["--playlist-end", "12345678"])],
    )

    proc = _run(ytdlp_replay, "--playlist-end", "12345679")

    assert proc.returncode == replay_script.REFUSED_EXIT
    assert len(ytdlp_replay.failures) == 1
    ytdlp_replay.clear_failures()


def test_call_beyond_the_recording_is_refused(
    ytdlp_replay: YtdlpReplay, fake_yt_dlp: Path
) -> None:
    _write_recording(_recording_path(ytdlp_replay), [_discovery_entry(["--simulate"])])

    first = _run(ytdlp_replay, "--simulate")
    second = _run(ytdlp_replay, "--simulate")

    assert (first.returncode, first.stdout) == (0, "first\n")
    assert second.returncode == replay_script.REFUSED_EXIT
    assert "beyond the 1 recorded" in ytdlp_replay.failures[0]
    assert not fake_yt_dlp.exists()
    ytdlp_replay.clear_failures()


def test_teardown_fails_a_recording_the_test_did_not_use_up(
    ytdlp_replay: YtdlpReplay,
) -> None:
    _write_recording(_recording_path(ytdlp_replay), [_discovery_entry(["--simulate"])])

    with pytest.raises(pytest.fail.Exception) as raised:
        ytdlp_replay.finalize()

    assert "made 0 yt-dlp call(s)" in str(raised.value)
    assert "PRISMIS_RECORD_YTDLP=1" in str(raised.value)
    # Leave the recording used up so the fixture's own teardown passes.
    _write_recording(_recording_path(ytdlp_replay), [])


def test_record_mode_runs_yt_dlp_and_the_recording_replays_it(
    ytdlp_replay: YtdlpReplay,
    fake_yt_dlp: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "live-out"
    out_dir.mkdir()
    args = ["--write-sub", "--output", str(out_dir / "%(id)s.%(ext)s"), "--", VIDEO_URL]
    monkeypatch.setenv(replay_script.MODE_ENV, "record")

    recorded = _run(ytdlp_replay, *args)

    assert recorded.returncode == 0
    assert fake_yt_dlp.exists(), "record mode must run yt-dlp"
    assert (out_dir / "abc123XYZ_-.en-orig.vtt").read_text() == VTT
    entries = json.loads(_recording_path(ytdlp_replay).read_text())
    assert entries == [
        {
            "args": replay_script.normalize_args(args),
            "returncode": 0,
            "stdout": "",
            "stderr": "recorded warning\n",
            "files": {"abc123XYZ_-.en-orig.vtt": VTT},
        }
    ]
    assert str(out_dir) not in json.dumps(entries), "the temp path must not be recorded"

    # Now replay it into a different directory, with yt-dlp gone.
    fake_yt_dlp.unlink()
    monkeypatch.setenv(replay_script.MODE_ENV, "replay")
    other = tmp_path / "other-out"
    replayed = _run(
        ytdlp_replay,
        "--write-sub",
        "--output",
        str(other / "%(id)s.%(ext)s"),
        "--",
        VIDEO_URL,
    )
    assert replayed.returncode == 0
    assert replayed.stderr == "recorded warning\n"
    assert (other / "abc123XYZ_-.en-orig.vtt").read_text() == VTT
    assert not fake_yt_dlp.exists(), "replay must not start yt-dlp"
