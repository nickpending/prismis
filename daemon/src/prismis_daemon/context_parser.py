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
            group belongs to the section above the group.
        fences: (line number, stripped line) for every line that opens or closes a
            code fence.
    """

    bullets: dict[str, list[tuple[str, str]]] = field(
        default_factory=lambda: {heading: [] for heading in CONTEXT_COUNTED_SECTIONS}
    )
    fences: list[tuple[int, str]] = field(default_factory=list)

    def topics(self) -> dict[str, list[str]]:
        """The topic lists the context analyzer reads: "high", "medium" and "low", each
        holding the text of its section's `- ` bullets, empty bullets left out."""
        return {
            key: [
                text
                for marker, text in self.bullets[heading]
                if marker == "-" and text
            ]
            for heading, key in _PRIORITY_KEYS.items()
        }


def parse_context(content: str) -> ParsedContext:
    """Walk a context.md once, line by line.

    A line starting with # or ## opens a new section (it is one of the counted headings,
    or no section at all); a ### group stays in the section above it. A bullet counts only
    under a counted heading. Fence lines are recorded and never read as headings or
    bullets, and the walk does not skip the lines between them.
    """
    parsed = ParsedContext()
    current: str | None = None
    for number, line in enumerate(content.splitlines(), start=1):
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
