"""Service keys come from the environment, and only from the environment.

Success criteria covered:
  SC-2: a service whose api_key is env:NAME with NAME unset, one whose api_key is a
        literal value, and one with no api_key, each resolved for a call through the
        real resolver -- the first two raise naming the service and the variable, the
        keyless one calls with no key.
  SC-3: no apiconf anywhere in the daemon or CLI source, pyproject.toml or uv.lock, and
        the path of the retired shared service file appears only in migrate-config.

Real collaborators: the real resolver over a real config.toml, and a real local HTTP
server standing in for the provider (the one boundary permitted to fake).
"""

import http.server
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from prismis_daemon import llm_client
from prismis_daemon.llm_client import ConfigError

_REPO = Path(__file__).parent.parent.parent.parent
_RETIRED_LIBRARY = "api" + "conf"
_RETIRED_SHARED_DIR = "llm" + "-core"


def _add_service(name: str, base_url: str, api_key: str | None) -> None:
    config_path = Path(os.environ["XDG_CONFIG_HOME"]) / "prismis" / "config.toml"
    block = f'\n[services.{name}]\nbase_url = "{base_url}/v1"\nmodel = "stub-model"\n'
    if api_key is not None:
        block += f'api_key = "{api_key}"\n'
    config_path.write_text(config_path.read_text() + block)


@contextmanager
def _provider() -> Iterator[tuple[str, list[str | None]]]:
    """A local chat-completions server; yields its URL and the Authorization headers seen."""
    seen: list[str | None] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            seen.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps(
                {
                    "id": "x",
                    "object": "chat.completion",
                    "model": "stub-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_unset_key_variable_is_refused_naming_service_and_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    SC-2: api_key = "env:NAME" with NAME unset raises, and the message carries both the
    service name and the variable so the operator knows what to export.
    BREAKS: the call goes out keyless or with a placeholder, or the error says only
    "not found" and the operator cannot tell which service or variable.
    """
    monkeypatch.delenv("SVC_KEY_TEST_UNSET", raising=False)
    _add_service("needs-key", "http://127.0.0.1:1", "env:SVC_KEY_TEST_UNSET")

    with pytest.raises(ConfigError) as err:
        llm_client.load_api_key(llm_client.resolve_service("needs-key"))
    assert "needs-key" in str(err.value)
    assert "SVC_KEY_TEST_UNSET" in str(err.value)

    with pytest.raises(ConfigError, match="SVC_KEY_TEST_UNSET"):
        llm_client.complete("hi", service="needs-key")


def test_empty_key_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    SC-2: an exported-but-empty variable is not a key.
    BREAKS: a blank `.env` line (`OPENROUTER_API_KEY=`) sends an empty bearer token and
    the provider's 401 is blamed on the provider.
    """
    monkeypatch.setenv("SVC_KEY_TEST_EMPTY", "")
    _add_service("empty-key", "http://127.0.0.1:1", "env:SVC_KEY_TEST_EMPTY")

    with pytest.raises(ConfigError, match="SVC_KEY_TEST_EMPTY"):
        llm_client.load_api_key(llm_client.resolve_service("empty-key"))


def test_literal_key_in_config_is_refused_and_never_echoed() -> None:
    """
    SC-2: a literal value in api_key raises naming the service, and the literal does not
    appear in the message (error text ends up in logs).
    BREAKS: a provider key pasted into config.toml works silently, so keys live in a
    config file; or the error repeats the secret into the log.
    """
    secret = "sk-literal-secret-0123456789"
    _add_service("literal-key", "http://127.0.0.1:1", secret)

    with pytest.raises(ConfigError) as err:
        llm_client.load_api_key(llm_client.resolve_service("literal-key"))
    assert "literal-key" in str(err.value)
    assert "env:" in str(err.value)
    assert secret not in str(err.value)

    with pytest.raises(ConfigError, match="literal-key"):
        llm_client.complete("hi", service="literal-key")


def test_set_key_variable_reaches_the_provider_as_the_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    SC-2 (positive side): the variable's value is what the provider receives.
    BREAKS: the env: reference resolves but a different value (or a placeholder) is sent.
    """
    monkeypatch.setenv("SVC_KEY_TEST_SET", "sk-from-environment")
    with _provider() as (url, seen):
        _add_service("keyed", url, "env:SVC_KEY_TEST_SET")
        llm_client.complete("hi", service="keyed")
    assert seen == ["Bearer sk-from-environment"]


def test_keyless_service_calls_without_any_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    SC-2: a service with no api_key resolves to no key and the call succeeds, with no
    provider key variable set anywhere in the environment.
    BREAKS: local-model-server users are forced to invent a key variable.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with _provider() as (url, seen):
        _add_service("keyless", url, None)
        assert llm_client.load_api_key(llm_client.resolve_service("keyless")) is None
        result = llm_client.complete("hi", service="keyless")
    assert result.text == "ok"
    assert len(seen) == 1
    assert "sk-" not in (seen[0] or "")


def test_unknown_service_names_it_and_where_to_define_it() -> None:
    """
    An undefined service is a config.toml problem and says so.
    BREAKS: verify points the operator at a file prismis no longer reads.
    """
    with pytest.raises(ConfigError) as err:
        llm_client.resolve_service("nope")
    assert "nope" in str(err.value)
    assert "config.toml" in str(err.value)
    assert _RETIRED_SHARED_DIR not in str(err.value)


def test_retired_key_library_appears_nowhere_in_shipped_source_or_locks() -> None:
    """
    SC-3: the retired key library's name is absent from daemon/src, cli/src,
    daemon/pyproject.toml and daemon/uv.lock.
    BREAKS: an import, a comment or a lock stanza keeps the dependency alive.
    """
    roots = [_REPO / "daemon" / "src", _REPO / "cli" / "src"]
    files = [_REPO / "daemon" / "pyproject.toml", _REPO / "daemon" / "uv.lock"]
    for root in roots:
        files += [
            p for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".toml")
        ]
    assert len(files) > 10, "the scan found almost nothing; the roots are wrong"

    hits = [
        f"{f.relative_to(_REPO)}:{n}"
        for f in files
        for n, line in enumerate(f.read_text().splitlines(), 1)
        if _RETIRED_LIBRARY in line
    ]
    assert hits == []


def test_retired_shared_service_path_appears_only_in_migrate_config() -> None:
    """
    SC-3: the shared service file's directory name appears in daemon/src, cli/src,
    daemon/pyproject.toml and daemon/uv.lock only inside migrate-config's function.
    BREAKS: a second reader of the shared file brings back the cross-app coupling this
    change removed.
    """
    main_py = _REPO / "daemon" / "src" / "prismis_daemon" / "__main__.py"
    text = main_py.read_text()
    start = text.index("def migrate_config(")
    end = text.index("\n@app.command()", start)
    inside = text[start:end]
    assert _RETIRED_SHARED_DIR in inside, (
        "migrate-config no longer reads the shared file"
    )
    outside = text[:start] + text[end:]

    others = [_REPO / "daemon" / "pyproject.toml", _REPO / "daemon" / "uv.lock"]
    for root in (_REPO / "daemon" / "src", _REPO / "cli" / "src"):
        others += [
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix in (".py", ".toml") and p != main_py
        ]

    assert _RETIRED_SHARED_DIR not in outside, "outside migrate_config in __main__.py"
    assert [
        str(f.relative_to(_REPO))
        for f in others
        if _RETIRED_SHARED_DIR in f.read_text()
    ] == []
