"""The withholding rule's two renderings agree (injection-flag C13).

Invariant protected:
  - with the check enabled, `is_withheld` keeps exactly the items whose stored judgement
    says `flagged` is JSON false, and a SELECT on `KEEP_SQL` keeps exactly the same
    rows; with the check disabled nothing is withheld

The SQL runs in a real SQLite database over analyses written the way Storage writes
them (json.dumps).
"""

import json
import sqlite3
from typing import Any

from prismis_daemon.publisher import KEEP_SQL, is_withheld

_JUDGED = {"score": 0.1, "chunk": 0, "chunks": 1, "start": 0, "end": 10}

# name -> the stored analysis (None is a NULL column).
_ANALYSES: dict[str, Any] = {
    "no-key": {"summary": "never checked"},
    "flagged-true": {"injection": {**_JUDGED, "flagged": True}},
    "flagged-false": {"injection": {**_JUDGED, "flagged": False}},
    "flagged-string-false": {"injection": {**_JUDGED, "flagged": "false"}},
    "flagged-null": {"injection": {**_JUDGED, "flagged": None}},
    "injection-not-an-object": {"injection": "flagged"},
    # JSON 0 is not JSON false: a rendering that compared to 0 would keep this row.
    "flagged-zero": {"injection": {**_JUDGED, "flagged": 0}},
    "null-analysis": None,
}


def _kept_by_sql() -> set[str]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE content (id TEXT, analysis TEXT)")
    conn.executemany(
        "INSERT INTO content VALUES (?, ?)",
        [
            (name, None if analysis is None else json.dumps(analysis))
            for name, analysis in _ANALYSES.items()
        ],
    )
    rows = conn.execute(f"SELECT c.id FROM content c WHERE {KEEP_SQL}").fetchall()
    conn.close()
    return {row[0] for row in rows}


def test_both_renderings_keep_exactly_the_clean_judgement() -> None:
    """
    INVARIANT: the Python and SQL renderings keep the same rows: only the one whose
               judgement says flagged is JSON false
    BREAKS: the audio briefing and the context analyzer disagree about which items are
            safe to send, or a malformed judgement passes as clean
    """
    kept_by_python = {
        name
        for name, analysis in _ANALYSES.items()
        if not is_withheld(analysis, check_enabled=True)
    }

    assert kept_by_python == {"flagged-false"}
    assert _kept_by_sql() == kept_by_python


def test_nothing_is_withheld_when_the_check_is_not_configured() -> None:
    """
    INVARIANT: an install that cannot check items withholds none of them
    BREAKS: an install with no decisions service sends an empty briefing
    """
    assert [
        name
        for name, analysis in _ANALYSES.items()
        if is_withheld(analysis, check_enabled=False)
    ] == []
