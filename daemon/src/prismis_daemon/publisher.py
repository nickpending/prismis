"""Publisher text, and the one rule for withholding it from an instruction-following model.

Titles, summaries and content come from feeds prismis does not control. Every consumer
that hands them to a model cleans each field with `publisher_text` and frames it as
data. With the injection check configured, the audio briefing and the context analyzer
also leave out an item's text entirely unless its stored judgement says it is clean;
`is_withheld` is that rule for Python readers and `KEEP_SQL` the same rule for a query,
and daemon/tests/unit/test_publisher_unit.py holds the two equal.

`publisher_text` is a copy of cli/src/cli/agent.py's: the CLI's `open` runs without the
daemon installed and the daemon never imports the CLI, so each keeps its own, and
cli/tests/unit/test_publisher_parity_unit.py holds the two equal.
"""

import unicodedata

# The SQL condition, on a query that aliases the content table `c`, that keeps exactly
# the rows `is_withheld(analysis, check_enabled=True)` keeps: a judgement whose flagged
# is JSON false. json_type, not json_extract: JSON 0 compares equal to false there.
KEEP_SQL = "json_type(c.analysis, '$.injection.flagged') = 'false'"


def is_withheld(analysis: object, check_enabled: bool) -> bool:
    """Whether an item's publisher text must be kept from an instruction-following model.

    With the check enabled, an item is withheld unless its analysis carries an
    `injection` judgement whose `flagged` is exactly False: a flagged item, one never
    checked, and one whose judgement is malformed are all withheld. With the check not
    configured, no item can be checked, so none is withheld.
    """
    if not check_enabled:
        return False
    judgement = analysis.get("injection") if isinstance(analysis, dict) else None
    return not (isinstance(judgement, dict) and judgement.get("flagged") is False)


def publisher_text(value: str | None) -> str:
    """One line of printable text from a publisher-supplied field.

    Control, line-separator and paragraph-separator characters (Cc, Zl, Zp) become a
    space, so the field cannot end its line; format characters (Cf: bidi overrides,
    zero-width characters) are removed, so nothing reaches the agent that the reader
    cannot see; `<` and `>` are escaped, so the field cannot close the `<article>`
    block. Whitespace runs then collapse to single spaces and the ends are trimmed.
    """
    kept: list[str] = []
    for char in value or "":
        category = unicodedata.category(char)
        if category == "Cf":
            continue
        if category in ("Cc", "Zl", "Zp"):
            kept.append(" ")
        elif char == "<":
            kept.append("&lt;")
        elif char == ">":
            kept.append("&gt;")
        else:
            kept.append(char)
    return " ".join("".join(kept).split())
