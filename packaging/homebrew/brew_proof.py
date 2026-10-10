"""End-to-end proof of SC-1, SC-3 and SC-4 on a macOS host with Homebrew and network.

Not part of the gate (it installs torch): run it as a lane step and keep the output in
docs/work/homebrew-formulas/proof-brew.txt.

    uv run python brew_proof.py

It snapshots this tree into a scratch git repo, taps a throwaway local tap whose formulas
are the committed ones with `head` pointed at that snapshot, and then for each formula,
one at a time from a clean state:
  SC-1  `brew install --HEAD`, `brew test`, the expected bin/ file exists, and no other
        prismis formula is installed alongside.
  SC-3  (daemon) every package of `uv export --frozen --no-dev --no-emit-project` of
        daemon/uv.lock, markers evaluated for macOS, is installed in libexec at its version.
  SC-4  (daemon) the plist brew renders from the service block runs prismis-daemon with
        keep-alive and a log under the brew prefix, and the daemon started from that
        command sees OPENROUTER_API_KEY from $XDG_CONFIG_HOME/prismis/.env (a run without
        the file is the control: it must report the variable as not set).
"""

import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import canonicalize_name

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TAP = "prismis-local/prismis"
BINS = {"prismis-tui": "prismis", "prismis-cli": "prismis-cli", "prismis-daemon": "prismis-daemon"}
MACOS_ENV = {
    "sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix",
    "platform_machine": "arm64", "implementation_name": "cpython",
    "platform_python_implementation": "CPython", "python_version": "3.14",
    "python_full_version": "3.14.0",
}

failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {label}", flush=True)
    if not ok:
        failures.append(label)


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # type: ignore[call-overload, no-any-return]


def prismis_installed() -> list[str]:
    return [f for f in run(["brew", "list", "--formula"]).stdout.split() if f.startswith("prismis")]


def snapshot(dest: Path) -> None:
    shutil.copytree(
        REPO, dest,
        ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", ".claude", "__pycache__"),
    )
    for step in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=proof@example.invalid", "-c", "user.name=proof", "commit", "-qm", "snapshot"]):
        subprocess.run(["git", *step], cwd=dest, check=True, capture_output=True)


def write_tap_formulas(snap: Path) -> None:
    repo = run(["brew", "--repository"]).stdout.strip()
    formula_dir = Path(repo) / "Library" / "Taps" / TAP.replace("/", "/homebrew-") / "Formula"
    for name in BINS:
        text = (HERE / f"{name}.rb").read_text()
        text = re.sub(r"^  head .*$", f'  head "file://{snap}", using: :git, branch: "main"', text, flags=re.M)
        (formula_dir / f"{name}.rb").write_text(text)


def locked_versions(daemon: Path) -> dict[str, str]:
    export = run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes",
                  "--no-header", "--project", str(daemon)], check=True).stdout
    locked: dict[str, str] = {}
    for line in export.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;]+)\s*(?:;\s*(.*))?$", line)
        if m and (not m[3] or Marker(m[3]).evaluate(MACOS_ENV)):
            locked[canonicalize_name(m[1])] = m[2]
    return locked


def prove_sc3() -> None:
    prefix = run(["brew", "--prefix", "prismis-daemon"]).stdout.strip()
    py = f"{prefix}/libexec/bin/python"
    freeze = run(["uv", "pip", "list", "--python", py, "--format", "freeze"], check=True).stdout
    installed: dict[str, str] = {canonicalize_name(a): b for a, b in (ln.split("==") for ln in freeze.splitlines() if "==" in ln)}
    locked: dict[str, str] = dict(locked_versions(REPO / "daemon"))
    bad = {k: (v, installed.get(k)) for k, v in locked.items() if installed.get(k) != v}
    check(bool(locked) and not bad, f"SC-3 {len(locked)} locked packages installed at locked versions {bad or ''}")


def prove_sc4(scratch: Path) -> None:
    ruby = 'puts Formulary.factory("%s/prismis-daemon").service.to_plist' % TAP
    plist = plistlib.loads(run(["brew", "ruby", "-e", ruby], check=True).stdout.encode())
    cmd = plist["ProgramArguments"]
    brew_prefix = run(["brew", "--prefix"]).stdout.strip()
    check(Path(cmd[0]).name == "prismis-daemon", f"SC-4 plist runs {cmd}")
    check(plist.get("KeepAlive") is True, "SC-4 plist KeepAlive")
    log = plist.get("StandardOutPath", "")
    check(log.startswith(brew_prefix) and plist.get("StandardErrorPath", "").startswith(brew_prefix),
          f"SC-4 plist logs under the brew prefix: {log}")

    cfg, data = scratch / "cfg", scratch / "data"
    cfg.mkdir()
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env.update(XDG_CONFIG_HOME=str(cfg), XDG_DATA_HOME=str(data))

    def started() -> str:
        r = run([*cmd, "--once"], env=env, timeout=180)
        return r.stdout + r.stderr

    started()  # first run writes config.toml and exits
    control = started()
    check("OPENROUTER_API_KEY" in control and "not set" in control,
          "SC-4 control: without .env the daemon reports OPENROUTER_API_KEY as not set")
    (cfg / "prismis" / ".env").write_text("OPENROUTER_API_KEY=probe-key\n")
    seen = started()
    check("not set" not in seen and "Starting Prismis daemon" in seen,
          "SC-4 with .env the daemon sees OPENROUTER_API_KEY and starts")


def main() -> int:
    if run(["brew", "list", "--formula"]).returncode != 0 or prismis_installed():
        print("refusing to run: brew missing or a prismis formula is already installed")
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="prismis-brew-proof-"))
    snap = scratch / "src"
    run(["brew", "tap-new", TAP], check=True)
    try:
        snapshot(snap)
        write_tap_formulas(snap)
        for name, binary in BINS.items():
            print(f"== {name}", flush=True)
            inst = run(["brew", "install", "--HEAD", f"{TAP}/{name}"])
            check(inst.returncode == 0, f"SC-1 {name} installs with --HEAD")
            if inst.returncode == 0:
                test = run(["brew", "test", f"{TAP}/{name}"])
                check(test.returncode == 0, f"SC-1 {name} brew test passes")
                prefix = run(["brew", "--prefix", name]).stdout.strip()
                check(Path(prefix, "bin", binary).exists(), f"SC-1 {name} provides bin/{binary}")
                check(prismis_installed() == [name], f"SC-1 {name} installed alone: {prismis_installed()}")
                if name == "prismis-daemon":
                    prove_sc3()
                    prove_sc4(scratch)
            run(["brew", "uninstall", "--force", name])
            check(prismis_installed() == [], f"{name} uninstalled cleanly")
    finally:
        run(["brew", "untap", TAP])
        shutil.rmtree(scratch, ignore_errors=True)
    print("proof: FAIL" if failures else "proof: PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
