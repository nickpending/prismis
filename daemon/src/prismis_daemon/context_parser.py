"""The one reader of context.md.

`verify`'s context check, `check_context_md` and the context analyzer all read the file
through `parse_context` below, so a bullet is counted, listed and checked by the same walk.
"""

from dataclasses import dataclass, field

# Sections the daemon requires in a context.md. The `verify` context check and the CLI's
# bootstrap prompt both answer to this one list.
REQUIRED_CONTEXT_SECTIONS: tuple[str, ...] = (
    "## High Priority Topics",
    "## Medium Priority Topics",
    "## Low Priority Topics",
)

# The four headings a context.md carries, in the order the bootstrap prompt asks for them.
CONTEXT_COUNTED_SECTIONS: tuple[str, ...] = (
    *REQUIRED_CONTEXT_SECTIONS,
    "## Not Interested",
)

# Analyzer key for each priority heading.
_PRIORITY_KEYS: dict[str, str] = {
    "## High Priority Topics": "high",
    "## Medium Priority Topics": "medium",
    "## Low Priority Topics": "low",
}


@dataclass
class ParsedContext:
    """What one walk over a context.md found.

    Attributes:
        bullets: For each of the four counted headings, its bullets in file order as
            (marker, text) pairs, where marker is "-" or "*". A bullet under a ###
            group belongs to the section above the group. A # or ## line ends the
            section. These feed `check_context_md`.
        fences: (line number, stripped line) for every line that opens or closes a
            code fence.
        priority_topics: The context analyzer's "high", "medium" and "low" lists. They
            keep the section boundaries the analyzer always had, which differ from
            `bullets`' at the edges: see `parse_context`.
    """

    bullets: dict[str, list[tuple[str, str]]] = field(
        default_factory=lambda: {heading: [] for heading in CONTEXT_COUNTED_SECTIONS}
    )
    fences: list[tuple[int, str]] = field(default_factory=list)
    priority_topics: dict[str, list[str]] = field(
        default_factory=lambda: {key: [] for key in _PRIORITY_KEYS.values()}
    )

    def topics(self) -> dict[str, list[str]]:
        """The topic lists the context analyzer reads: "high", "medium" and "low"."""
        return {key: list(topics) for key, topics in self.priority_topics.items()}


# Analyzer section states: not yet opened, opened (the next non-blank line is its first
# content line), reading, finished.
_WAITING, _OPENED, _READING, _DONE = range(4)


def parse_context(content: str) -> ParsedContext:
    """Walk a context.md once, line by line, filling both views of it.

    The counted view (`bullets`, `fences`): a line starting with # or ## opens a new
    section (one of the counted headings, or none); a ### group stays in the section
    above it; a bullet counts only under a counted heading; fence lines are recorded and
    never read as headings or bullets.

    The analyzer view (`priority_topics`) keeps the rules of the regex it replaced, so
    its lists do not move: a priority section opens at the first line that ends with its
    `## ... Priority Topics` heading (the heading may follow other text, such as the
    extra # of a ### line) and runs to the next line that starts with `## `, which a
    single # heading does not end. The first non-blank line after the heading can never
    end the section, so an empty section runs on through the next heading's bullets.
    Only `- ` bullets are listed.
    """
    parsed = ParsedContext()
    current: str | None = None
    number = 0
    state = dict.fromkeys(_PRIORITY_KEYS.values(), _WAITING)
    headings = {key: heading for heading, key in _PRIORITY_KEYS.items()}
    # `\n` delimits the analyzer view's lines; the counted view splits each of those
    # further the way `str.splitlines` does.
    units = content.split("\n")
    for index, unit in enumerate(units):
        for key, heading in headings.items():
            if state[key] == _READING and unit.startswith("## "):
                state[key] = _DONE
            elif state[key] in (_OPENED, _READING) and unit.strip():
                state[key] = _READING
                text = unit.strip()
                if text.startswith("- ") and text[2:].strip():
                    parsed.priority_topics[key].append(text[2:].strip())
            elif (
                state[key] == _WAITING
                and index < len(units) - 1
                and unit.rstrip().endswith(heading)
            ):
                state[key] = _OPENED
        for line in unit.splitlines() or [""]:
            number += 1
            stripped = line.strip()
            if stripped.startswith("```"):
                parsed.fences.append((number, stripped))
            elif stripped.startswith("#"):
                if len(stripped) - len(stripped.lstrip("#")) <= 2:
                    current = stripped if stripped in parsed.bullets else None
            elif current is not None and stripped.startswith(("- ", "* ")):
                parsed.bullets[current].append((stripped[0], stripped[2:].strip()))
    return parsed


def check_context_md(content: str) -> tuple[list[str], dict[str, int]]:
    """Check a saved context.md and name what is wrong with it.

    Args:
        content: The text of the file

    Returns:
        (problems, counts). Each problem is one named string: a missing required
        section, or a line that opens or closes a code fence. counts maps each of the
        four context headings to the number of bullets under it.
    """
    parsed = parse_context(content)
    problems = [
        f"missing required section: {section}"
        for section in REQUIRED_CONTEXT_SECTIONS
        if section not in content
    ]
    problems.extend(
        f"code fence on line {number}: {stripped}" for number, stripped in parsed.fences
    )
    counts = {heading: len(items) for heading, items in parsed.bullets.items()}
    return problems, counts
