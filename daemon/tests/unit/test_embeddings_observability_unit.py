"""Unit tests for the embedding link's observability record.

Invariants protected:
  - A successful embedding emits embedding.generate with a success status, the model
    name, the real vector dimension and a duration
  - A failure emits a distinguishable error event and still re-raises, so callers keep
    the behavior they have today

Success criteria covered:
  SC-5 (the dark links emit records)

Real collaborators throughout: the real sentence-transformers model for the success
path (huggingface_hub resolves its cache at import, before the env seal, so this uses
the machine/CI cache and makes no network call), and a real invalid local model path
for the failure path, which raises before any lookup is attempted.
"""

import uuid

import pytest

from prismis_daemon.embeddings import Embedder
from prismis_daemon.observability import get_logger, reset_logger, set_run_id
from prismis_daemon.verify_chain import read_run_events


@pytest.fixture(autouse=True)
def _fresh_observability():
    """Bind the observability logger to this test's sealed data dir, and unbind after.

    The logger is a module-level singleton that caches the base directory it resolved
    when first constructed, and every test gets a different XDG_DATA_HOME — without the
    reset a test reads a directory an earlier test established (and, where that earlier
    test removed it, one that no longer exists). The run id is a module-level global for
    the same reason it is reset here: pytest runs the suite in one process.
    """
    reset_logger()
    yield
    set_run_id(None)
    reset_logger()


def _events_for(run_id: str) -> list[dict]:
    """This run's events only.

    The JSONL is shared by every test in the process, so selection is by a run id
    generated here — a value that cannot appear in any line written before this test.
    """
    return read_run_events(get_logger().base_dir, run_id)


def test_successful_embedding_emits_success_event() -> None:
    """
    SC-5: a generated embedding leaves a record carrying outcome, model and dimension.
    BREAKS: the embed link is dark — the chain (and the unattended daemon) cannot tell
    an embedding that ran from one that never happened.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    vector = Embedder().generate_embedding("a short piece of text", title="A title")

    events = [e for e in _events_for(run_id) if e["event"] == "embedding.generate"]
    assert len(events) == 1, f"expected exactly one embedding event, got {events}"
    event = events[0]
    assert event["status"] == "success"
    assert event["model"] == "all-MiniLM-L6-v2"
    assert event["dimension"] == len(vector)
    assert isinstance(event["duration_ms"], int)


def test_failed_embedding_emits_error_event_and_re_raises() -> None:
    """
    SC-5: a failure is a distinguishable event, not silence — and the exception still
    reaches the caller, because every orchestrator call site relies on catching it.

    The model name is a path that cannot resolve locally, so sentence-transformers
    raises for real without any lookup.

    BREAKS: an embedding failure is indistinguishable from an embedding that never ran,
    or the re-raise is dropped and the orchestrator silently believes it succeeded.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    with pytest.raises(FileNotFoundError):
        Embedder(model_name="not/a/real/model").generate_embedding("text")

    events = [e for e in _events_for(run_id) if e["event"] == "embedding.generate"]
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "error"
    assert event["model"] == "not/a/real/model"
    assert "not/a/real/model" in event["error"]
    assert isinstance(event["duration_ms"], int)
