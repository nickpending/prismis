"""Pin the formula renderer against the lock, the tag fill and the user-facing text.

The committed formulas, cli/uv.lock and README.md are read from the tree; mutations are
made on copies in tmp_path.
"""

import hashlib
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

import render

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
CLI_LOCK = REPO / "cli" / "uv.lock"
FORMULAS = ("prismis-tui", "prismis-cli", "prismis-daemon")

RESOURCE_RE = re.compile(
    r'resource "(?P<name>[^"]+)" do\n    url "(?P<url>[^"]+)"\n    sha256 "(?P<sha>[0-9a-f]{64})"\n  end'
)


def parse_resources(text: str) -> dict[str, tuple[str, str]]:
    return {m["name"]: (m["url"], m["sha"]) for m in RESOURCE_RE.finditer(text)}


def lock_packages(lock: Path) -> dict[str, dict]:
    data = tomllib.loads(lock.read_text())
    return {p["name"]: p for p in data["package"]}


def committed_resources_block(formula: Path) -> str:
    text = formula.read_text()
    m = re.search(
        rf"^  {re.escape(render.BEGIN)}\n(.*?)^  {re.escape(render.END)}\n",
        text,
        re.S | re.M,
    )
    assert m, "resource markers missing from the formula"
    return m.group(1)


@pytest.fixture
def formula_dir(tmp_path: Path) -> Path:
    d = tmp_path / "formulas"
    d.mkdir()
    for name in FORMULAS:
        shutil.copy(HERE / f"{name}.rb", d / f"{name}.rb")
    return d


# ------------------------------------------------------------------ SC-2


def test_resources_match_lock_sdist_url_and_hash() -> None:
    rendered = parse_resources(render.render_resources(CLI_LOCK))
    packages = lock_packages(CLI_LOCK)
    assert rendered, "no resources rendered"
    for name, (url, sha) in rendered.items():
        sdist = packages[name]["sdist"]
        assert url == sdist["url"]
        assert f"sha256:{sha}" == sdist["hash"]


def test_resources_hold_client_dependencies_and_exclude_the_rest() -> None:
    names = set(parse_resources(render.render_resources(CLI_LOCK)))
    for needed in ("httpx", "httpcore", "typer", "rich", "python-dotenv",
                   "h11", "anyio", "certifi", "idna", "shellingham", "pygments"):
        assert needed in names, f"client dependency {needed} missing"
    # [local] extra and Windows-only packages must not be rendered; neither must the
    # project itself (the formula installs it from the tarball).
    for excluded in ("prismis-daemon", "prismis-cli", "colorama", "torch", "pytest", "mypy"):
        assert excluded not in names, f"{excluded} must not be a resource"


def test_committed_cli_formula_resources_equal_the_render() -> None:
    committed = committed_resources_block(HERE / "prismis-cli.rb")
    assert committed == render.render_resources(CLI_LOCK)


def test_changed_locked_version_makes_the_render_differ_from_committed(tmp_path: Path) -> None:
    text = CLI_LOCK.read_text()
    packages = lock_packages(CLI_LOCK)
    url = packages["httpx"]["sdist"]["url"]
    mutated = tmp_path / "uv.lock"
    mutated.write_text(text.replace(url, url.replace(".tar.gz", "x.tar.gz"), 1))
    committed = committed_resources_block(HERE / "prismis-cli.rb")
    assert render.render_resources(mutated) != committed


def test_check_exits_nonzero_when_committed_resources_are_stale(tmp_path: Path, formula_dir: Path) -> None:
    mutated = tmp_path / "uv.lock"
    text = CLI_LOCK.read_text()
    url = lock_packages(CLI_LOCK)["httpx"]["sdist"]["url"]
    mutated.write_text(text.replace(url, url.replace(".tar.gz", "x.tar.gz"), 1))
    assert render.main(["check", "--formula-dir", str(formula_dir), "--lock", str(CLI_LOCK)]) == 0
    assert render.main(["check", "--formula-dir", str(formula_dir), "--lock", str(mutated)]) == 1


def test_package_without_sdist_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    text = CLI_LOCK.read_text()
    sdist = lock_packages(CLI_LOCK)["h11"]["sdist"]
    stripped = re.sub(r'^sdist = \{ url = "' + re.escape(sdist["url"]) + r'".*\}\n', "", text, flags=re.M)
    assert stripped != text
    lock.write_text(stripped)
    with pytest.raises(ValueError, match="h11"):
        render.render_resources(lock)


# ------------------------------------------------------------------ SC-5


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    b, a = before.splitlines(), after.splitlines()
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(b, a, strict=True) if x != y]


def test_fill_changes_only_url_sha256_and_version(formula_dir: Path, tmp_path: Path) -> None:
    tarball = tmp_path / "t.tar.gz"
    tarball.write_bytes(b"prismis release bytes")
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    before = {n: (formula_dir / f"{n}.rb").read_text() for n in FORMULAS}

    render.fill_formulas(formula_dir, "v1.2.3", tarball)

    for n in FORMULAS:
        after = (formula_dir / f"{n}.rb").read_text()
        diff = changed_lines(before[n], after)
        assert len(diff) == 3, f"{n}: expected exactly url, sha256, version to change"
        assert [y for _, y in diff] == [
            '  url "https://github.com/nickpending/prismis/archive/refs/tags/v1.2.3.tar.gz"',
            f'  sha256 "{sha}"',
            '  version "1.2.3"',
        ]


def test_fill_leaves_resource_urls_and_hashes_alone(formula_dir: Path, tmp_path: Path) -> None:
    tarball = tmp_path / "t.tar.gz"
    tarball.write_bytes(b"x")
    before = parse_resources((formula_dir / "prismis-cli.rb").read_text())
    render.fill_formulas(formula_dir, "v9.9.9", tarball)
    assert parse_resources((formula_dir / "prismis-cli.rb").read_text()) == before
    assert before


def test_fill_refuses_a_formula_missing_a_field(formula_dir: Path, tmp_path: Path) -> None:
    tarball = tmp_path / "t.tar.gz"
    tarball.write_bytes(b"x")
    f = formula_dir / "prismis-tui.rb"
    f.write_text(re.sub(r'^  version ".*"\n', "", f.read_text(), flags=re.M))
    with pytest.raises(ValueError, match="version"):
        render.fill_formulas(formula_dir, "v1.0.0", tarball)


# ------------------------------------------------------------------ SC-6


def caveats(name: str) -> str:
    m = re.search(r"def caveats\n    <<~EOS\n(.*?)\n    EOS", (HERE / f"{name}.rb").read_text(), re.S)
    assert m, f"{name} has no caveats"
    return m.group(1)


@pytest.mark.parametrize("name", ["prismis-tui", "prismis-cli"])
def test_client_caveats_name_remote_step_and_check(name: str) -> None:
    text = caveats(name)
    assert "[remote]" in text
    assert "url" in text and "key" in text
    assert "~/.config/prismis/config.toml" in text
    assert "prismis-cli list --limit 1" in text


def test_daemon_caveats_name_the_first_run_steps() -> None:
    text = caveats("prismis-daemon")
    for step in ("OPENROUTER_API_KEY", "~/.config/prismis/.env", "prismis-cli context bootstrap",
                 "prismis-daemon verify", "brew services start prismis-daemon"):
        assert step in text, f"daemon caveats omit {step}"


def test_readme_installation_leads_with_the_three_brew_commands() -> None:
    readme = (REPO / "README.md").read_text()
    m = re.search(r"^## [^\n]*Installation[^\n]*\n(.*?)^## ", readme, re.S | re.M)
    assert m, "README has no Installation section"
    section = m.group(1)
    cmds = [
        "brew install nickpending/prismis/prismis-tui",
        "brew install nickpending/prismis/prismis-cli",
        "brew install nickpending/prismis/prismis-daemon",
    ]
    positions = [section.find(c) for c in cmds]
    assert all(p >= 0 for p in positions), "a brew command is missing from Installation"
    assert section.find("make install") > max(positions), "make install must follow the brew commands"


# ------------------------------------------------------------ SC-1, SC-3, SC-4 (standing parts)
# The install, `brew test`, libexec-versus-lock and launchd/.env proofs need Homebrew and the
# network; brew_proof.py runs them and its last recorded run is
# docs/work/homebrew-formulas/proof-brew.txt. These tests pin what is checkable without brew:
# the pin filter the daemon install runs, the service block, and install-alone.


def test_daemon_pin_filter_drops_no_requirement_line() -> None:
    text = (HERE / "prismis-daemon.rb").read_text()
    m = re.search(r"lines\.grep\(/(?P<rx>.+)/\)", text)
    assert m, "the daemon formula no longer filters the export with lines.grep"
    rx = re.compile(m["rx"].replace(r"\A", "^"))
    export = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "--no-header",
         "--project", str(REPO / "daemon")],
        capture_output=True, text=True, check=True,
    ).stdout
    lines = [ln for ln in export.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    dropped = [ln for ln in lines if not rx.match(ln)]
    assert lines
    assert not dropped, f"export lines the pin filter would silently drop: {dropped}"


def test_daemon_installs_pins_as_constraints_from_the_lock() -> None:
    text = (HERE / "prismis-daemon.rb").read_text()
    for needle in ('"--frozen"', '"--no-dev"', '"--no-emit-project"', '"--project", "daemon"',
                   '"--constraints"'):
        assert needle in text, f"daemon install lost {needle}"


def test_daemon_service_block_runs_daemon_with_keepalive_and_brew_prefix_log() -> None:
    text = (HERE / "prismis-daemon.rb").read_text()
    m = re.search(r"  service do\n(.*?)\n  end\n", text, re.S)
    assert m, "no service block"
    block = m.group(1)
    assert 'run [opt_bin/"prismis-daemon"]' in block
    assert "keep_alive true" in block
    assert 'log_path var/"log/' in block
    assert 'error_log_path var/"log/' in block


@pytest.mark.parametrize("name", FORMULAS)
def test_formula_installs_alone(name: str) -> None:
    text = (HERE / f"{name}.rb").read_text()
    deps = re.findall(r'^  depends_on "([^"]+)"', text, re.M)
    assert deps
    assert not [d for d in deps if "prismis" in d], f"{name} pulls in another prismis formula"
    assert re.search(r'^  head "https://github.com/nickpending/prismis.git", branch: "main"$', text, re.M)
