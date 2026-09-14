"""The chain driven end to end with no credentials, no cost and no third party.

Invariant protected:
  - `verify --chain` runs the real orchestrator through every default link and reports
    each one, against services this test stands up itself

Success criteria covered:
  SC-1 (the production orchestrator drives it), SC-3 (every link reports), SC-6
  (defaults spend the light service only), SC-8 (provable here, in the gate)

Why this exists: the chain was runnable only where a full config with real credentials
lived, which in practice meant the deploy host. So the one thing nobody could exercise
from a clone was the composition itself — and the bug the first real run found (deep
extraction billing the deep service while the report said "skipped-by-flag") was a
wiring bug a local run would have caught for free.

What this does NOT prove: summary quality, provider-specific behaviour, or cost
reporting. Those need a real provider and stay with the deploy-host run.

Real collaborators throughout: real RSSFetcher over HTTP, real Storage, real Embedder,
real DaemonOrchestrator. The LLM endpoint is the one collaborator the constitution
permits standing in for, and it is a real HTTP server, not a patched object.
"""

import os
from pathlib import Path

from rich.console import Console

from prismis_daemon.verify_chain import execute_chain

_DEFAULT_LINKS = ("fetch", "dedup", "summarize", "evaluate", "store", "embed")


def _write_llm_services(cfg_home: Path, base_url: str) -> None:
    """Point a named llm-core service at the stub. key_required=false means no secret."""
    d = cfg_home / "llm-core"
    d.mkdir(parents=True, exist_ok=True)
    (d / "services.toml").write_text(
        'default_service = "prismis-verify-stub"\n'
        "[services.prismis-verify-stub]\n"
        'adapter = "openai"\n'
        f'base_url = "{base_url}/v1"\n'
        "key_required = false\n"
        'default_model = "stub-model"\n'
    )


def _point_daemon_config_at_stub(cfg_home: Path) -> None:
    """Point both services at the stub and turn auto_extract on.

    Deliberately mirrors the deploy host: a deep service IS configured and
    auto_extract is "high". Disabling the deep service instead would make
    build_orchestrator return deep_extractor=None whatever the --full flag says, so a
    regression in that gate could not manifest — the test would be asserting against a
    world the bug cannot occur in. Verified: with deep_service off, reverting the gate
    leaves this test green."""
    path = cfg_home / "prismis" / "config.toml"
    text = path.read_text()
    out = []
    for line in text.splitlines():
        if line.startswith("light_service"):
            out.append('light_service = "prismis-verify-stub"')
        elif line.startswith(("deep_service", "# deep_service")):
            out.append('deep_service = "prismis-verify-stub"')
        elif line.startswith("auto_extract"):
            out.append('auto_extract = "high"')
        else:
            out.append(line)
    path.write_text("\n".join(out) + "\n")


def test_chain_runs_every_default_link_against_local_services(
    local_pipeline_stub: str, isolated_xdg_env: Path, tmp_path: Path
) -> None:
    """
    INVARIANT: The chain drives all six default links and reports each, with no
               credential, no third party and no spend
    BREAKS: The composition is exercisable only on the deploy host, so a clone cannot
            run it and a wiring regression is caught by a billed run or not at all
    """
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    _write_llm_services(cfg_home, local_pipeline_stub)
    _point_daemon_config_at_stub(cfg_home)
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")

    exit_code, links = execute_chain(
        f"{local_pipeline_stub}/feed.xml", "rss", full=False, console=Console(quiet=True)
    )

    by_name = {link.name: link for link in links}
    assert set(_DEFAULT_LINKS).issubset(by_name), (
        f"every default link must be reported, got {sorted(by_name)}"
    )
    for name in _DEFAULT_LINKS:
        assert by_name[name].status == "ran", (
            f"{name} reported {by_name[name].status!r}: {by_name[name].detail}"
        )

    # The config here mirrors the deploy host — deep service configured, auto_extract
    # "high" — so the run is realistic rather than one where deep extraction was
    # switched off. The gate itself is guarded structurally by
    # test_verify_chain_source_unit.py, which asserts the extractor is not even built
    # without --full; that is the test which reddens when the gate is reverted.
    assert by_name["deep_extract"].status == "skipped-by-flag"
    assert by_name["notify"].status == "skipped-by-flag"
    assert exit_code == 0, f"a clean local run must exit 0, got {exit_code}"

    assert by_name["summarize"].model, "the answering model must be reported"
