"""Only the content's own check sets its injection judgement (injection-flag X-1-1).

Invariants protected:
  - store_injection_judgement stores a judgement only while the row still holds the
    content that was checked, and writes only the injection key
  - update_analysis and update_priority, which write a whole analysis read earlier,
    keep the row's own injection judgement, or its absence, whatever the dict says

Real Storage over the sealed test database.
"""

from pathlib import Path
from typing import Any

from conftest import add_new_content
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

_JUDGEMENT = {
    "score": 0.02,
    "flagged": False,
    "chunk": 0,
    "chunks": 1,
    "start": 0,
    "end": 20,
}


def _item(storage: Storage, analysis: dict[str, Any]) -> str:
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    return add_new_content(
        storage,
        ContentItem(
            source_id=source_id,
            external_id="item-1",
            title="An item",
            url="https://example.com/a",
            content="The text that was checked.",
            summary="Summary.",
            analysis=analysis,
        ),
    )


def test_a_judgement_is_stored_only_on_the_content_it_was_made_on(
    test_db: Path,
) -> None:
    """
    INVARIANT: the judgement lands only while the stored content is the checked text,
               and leaves every other analysis key as the row holds it
    BREAKS: a judgement of old text vouches for content a refetch put in its place
    """
    storage = Storage(test_db)
    content_id = _item(storage, {"reading_summary": "kept"})

    assert not storage.store_injection_judgement(content_id, "Other text.", _JUDGEMENT)
    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert "injection" not in stored["analysis"]

    assert storage.store_injection_judgement(
        content_id, "The text that was checked.", _JUDGEMENT
    )
    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["analysis"] == {"reading_summary": "kept", "injection": _JUDGEMENT}


def test_whole_analysis_writes_keep_the_rows_own_judgement(test_db: Path) -> None:
    """
    INVARIANT: a write of an analysis read earlier neither brings back a judgement the
               row no longer has, nor drops or replaces the one it has
    BREAKS: a backfill or the extract endpoint, racing a refetch, re-plants a clean
            judgement of the old text on the new content
    """
    storage = Storage(test_db)
    content_id = _item(storage, {"reading_summary": "now"})
    stale = {"reading_summary": "later", "injection": {**_JUDGEMENT, "score": 0.01}}

    assert storage.update_analysis(content_id, stale)
    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["analysis"] == {"reading_summary": "later"}

    assert storage.update_priority(content_id, "high", stale)
    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["analysis"] == {"reading_summary": "later"}

    assert storage.store_injection_judgement(
        content_id, "The text that was checked.", _JUDGEMENT
    )
    assert storage.update_analysis(content_id, {"reading_summary": "again"})
    assert storage.update_priority(content_id, "low", dict(stale))
    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["analysis"] == {"reading_summary": "later", "injection": _JUDGEMENT}
