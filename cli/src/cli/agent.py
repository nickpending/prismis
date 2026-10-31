"""`prismis-cli open`: start the configured agent with a prompt naming one article.

The [agent] read, the prompt, and every launch live here and nowhere else; the TUI's
`A` and `:agent` run this command and show the one outcome line it writes.
"""

import dataclasses
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import typer

from .api_client import APIClient
from .remote import config_toml_path, explicit_remote_url, read_config_toml

TARGETS = ("claude", "pi", "herdr")
DEFAULT_KIND = "claude"
DEFAULT_PROMPT = (
    "I just read this. Help me dig into it: what matters, what is questionable, "
    "and where to go next."
)
NOT_CONFIGURED = (
    'No agent configured: set [agent] target = "claude", "pi" or "herdr" in config.toml'
)

# Covers `herdr agent start`'s own 30 s wait for the agent to be ready for input.
HERDR_TIMEOUT_SECONDS = 45


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    """The [agent] table with its defaults applied. `target` is None when unset."""

    target: str | None
    command: list[str]
    kind: str
    prompt: str


class OpenFailed(Exception):
    """A run that ends before the agent starts; its message is the outcome line."""


def load_agent_config() -> AgentConfig:
    """Read [agent] from config.toml and apply the defaults.

    Raises:
        RuntimeError: If config.toml cannot be read or parsed, [agent] is not a table,
            or one of its keys is not a string.
    """
    section = read_config_toml().get("agent", {})
    if not isinstance(section, dict):
        raise RuntimeError(f"Invalid {config_toml_path()}: [agent] must be a table")

    values: dict[str, str | None] = {}
    for key in ("target", "command", "kind", "prompt"):
        value = section.get(key)
        if value is not None and not isinstance(value, str):
            raise RuntimeError(
                f"Invalid {config_toml_path()}: [agent] {key} must be a string"
            )
        values[key] = value

    target = values["target"]
    command = values["command"] or target
    words: list[str] = command.split() if command else []
    return AgentConfig(
        target=target,
        command=words,
        kind=values["kind"] or DEFAULT_KIND,
        prompt=values["prompt"] or DEFAULT_PROMPT,
    )


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def build_prompt(entry: dict[str, Any], starter: str, remote_url: str | None) -> str:
    """The text the agent starts with: the article, how to fetch the rest, a starter.

    Whitespace runs in the title and summary collapse to single spaces. The fetch
    commands carry `--remote <url>` only when the launching CLI was given it; without
    it the agent's prismis-cli resolves the daemon from config.toml, as this one did.
    """
    item_id = entry.get("id", "")
    cli = "prismis-cli" if remote_url is None else f"prismis-cli --remote {remote_url}"
    summary = _one_line(entry.get("summary")) or "(none)"
    lines = [
        f"I'm reading an article in prismis (item {item_id}).",
        "",
        f"Title: {_one_line(entry.get('title'))}",
        f"URL: {entry.get('url', '')}",
        f"Summary: {summary}",
        "",
        "For more, run:",
        f"  {cli} get {item_id} --json   # the full analysis: reading summary, quotes, deep synthesis",
        f"  {cli} get {item_id} --raw    # the full article text or transcript",
        "",
        starter,
    ]
    return "\n".join(lines)


def run_herdr(args: list[str]) -> str:
    """Run one herdr call, bounded by HERDR_TIMEOUT_SECONDS, and return its stdout.

    Raises:
        OpenFailed: If herdr is missing, does not answer in time, or exits non-zero.
    """
    sub = " ".join(args[:2])
    try:
        proc = subprocess.run(
            ["herdr", *args],
            capture_output=True,
            text=True,
            timeout=HERDR_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise OpenFailed("Error: herdr not found") from e
    except subprocess.TimeoutExpired as e:
        raise OpenFailed(
            f"herdr {sub} did not answer within {HERDR_TIMEOUT_SECONDS} s"
        ) from e
    if proc.returncode != 0:
        detail = proc.stdout.strip() or proc.stderr.strip()
        raise OpenFailed(f"herdr {sub} failed: {detail}")
    return proc.stdout


def _herdr_field(args: list[str], path: tuple[str, ...]) -> object:
    """Run a herdr call and read the JSON value at `path` from its answer."""
    output = run_herdr(args)
    missing = OpenFailed(
        f"herdr {' '.join(args[:2])} answered without {'.'.join(path)}"
    )
    try:
        value: object = json.loads(output)
    except json.JSONDecodeError as e:
        raise missing from e
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise missing
        value = value[key]
    return value


def _open_in_herdr(name: str, kind: str, prompt: str) -> str:
    """Focus the article's live herdr agent, or start one in a new pane beside this one."""
    if os.environ.get("HERDR_ENV") != "1":
        raise OpenFailed("herdr is not available (HERDR_ENV is not set)")

    agents = _herdr_field(["agent", "list"], ("result", "agents"))
    if not isinstance(agents, list):
        raise OpenFailed("herdr agent list answered without result.agents")
    if any(isinstance(a, dict) and a.get("name") == name for a in agents):
        run_herdr(["agent", "focus", name])
        return f"{name} already open — focused it"

    split = ["pane", "split", "--current", "--direction", "right"]
    pane_id = _herdr_field(
        [*split, "--cwd", os.getcwd(), "--no-focus"], ("result", "pane", "pane_id")
    )
    if not isinstance(pane_id, str):
        raise OpenFailed("herdr pane split answered without result.pane.pane_id")
    run_herdr(["agent", "start", name, "--kind", kind, "--pane", pane_id, "--", prompt])
    return f"opened {name} in pane {pane_id}"


def _open(entry_id: str) -> tuple[int, str]:
    """Run the open sequence; return the exit code and the outcome line."""
    try:
        config = load_agent_config()
    except RuntimeError as e:
        return 1, f"Error: {e}"
    if config.target is None:
        return 1, NOT_CONFIGURED
    if config.target not in TARGETS:
        return 1, f'Unknown agent target "{config.target}". {NOT_CONFIGURED}'

    try:
        entry = APIClient().get_entry(entry_id)
    except RuntimeError as e:
        return 1, f"Error: {e}"
    prompt = build_prompt(entry, config.prompt, explicit_remote_url())

    if config.target == "herdr":
        name = f"prismis-{entry_id[:8].lower()}"
        try:
            return 0, _open_in_herdr(name, config.kind, prompt)
        except OpenFailed as e:
            return 1, str(e)

    if not config.command:
        return 1, f"Error: [agent] command for {config.target} is empty"
    try:
        # One argv element per word of `command`, then the prompt as one more: no
        # shell ever sees the article's text.
        code = subprocess.run([*config.command, prompt]).returncode
    except FileNotFoundError:
        return 1, f"Error: {config.command[0]} not found"
    return code, f"{config.target} session ended"


def open_entry(
    entry_id: str = typer.Argument(..., help="ID of the content entry to open"),
    status_file: str | None = typer.Option(
        None, "--status-file", hidden=True, help="Also write the outcome line here"
    ),
) -> None:
    """Start the agent [agent] names in config.toml with a prompt about one entry.

    Every run ends in one outcome line, printed to stdout on success and stderr on
    failure, and also written to --status-file when given (the TUI's channel).
    """
    code, line = _open(entry_id)
    if status_file is not None:
        Path(status_file).write_text(line + "\n")
    typer.echo(line, err=code != 0)
    raise typer.Exit(code)
