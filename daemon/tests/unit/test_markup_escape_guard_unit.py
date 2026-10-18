"""Every interpolation in a printed Rich markup f-string is escaped.

Invariant protected:
  - in daemon/src and cli/src, a `.print(...)` argument that is an f-string whose literal
    text carries a Rich markup tag wraps every interpolated value in `escape(...)`

Rich reads `[name]` inside a printed string as a style tag and drops it, and a value
carrying `[/x]` makes Rich raise MarkupError. External text -- exceptions, titles, paths --
must reach the user as written.

The scan reads f-strings only. A markup string built by concatenation or `.format` and
printed later is outside its sight; review owns that path.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SRC_ROOTS = (_REPO / "daemon" / "src", _REPO / "cli" / "src")

# [name], [/name], [/], [#hex], [bold red], [link=...] -- what Rich parses as a tag.
_MARKUP_TAG = re.compile(r"\[/?[A-Za-z#@][^\[\]]*\]|\[/\]")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    expression: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.expression}"


def _is_escape(node: ast.expr) -> bool:
    """True for a call to escape(...) or markup.escape(...), with no format spec on it."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "escape"
    return isinstance(func, ast.Attribute) and func.attr == "escape"


def _has_markup(fstring: ast.JoinedStr) -> bool:
    return any(
        isinstance(part, ast.Constant)
        and isinstance(part.value, str)
        and _MARKUP_TAG.search(part.value)
        for part in fstring.values
    )


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[Violation] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "print":
            args = [*node.args, *(kw.value for kw in node.keywords)]
            for arg in args:
                if isinstance(arg, ast.JoinedStr) and _has_markup(arg):
                    self._check(arg)
        self.generic_visit(node)

    def _check(self, fstring: ast.JoinedStr) -> None:
        for part in fstring.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            if part.conversion == -1 and part.format_spec is None and _is_escape(part.value):
                continue
            self.violations.append(
                Violation(self.path, part.value.lineno, ast.unparse(part.value))
            )


def scan(root: Path) -> list[Violation]:
    """Violations in every .py file under root, ordered by path and line."""
    found: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        scanner = _Scanner(path)
        scanner.visit(ast.parse(path.read_text(), filename=str(path)))
        found.extend(scanner.violations)
    return found


def test_markup_prints_escape_every_interpolation() -> None:
    """
    INVARIANT: no markup print in daemon/src or cli/src interpolates a bare value
    BREAKS: a config hint like 'Add [api] section' loses its section name, and a value
            carrying '[/x]' raises MarkupError inside an error handler
    """
    for root in _SRC_ROOTS:
        assert root.is_dir(), f"source root missing: {root}"
    violations = [v for root in _SRC_ROOTS for v in scan(root)]
    assert not violations, "unescaped interpolation in a markup print:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_scan_fires_on_bare_value_and_ignores_the_rest(tmp_path: Path) -> None:
    """
    INVARIANT: the scan reports a bare value inside a markup print and nothing else
    BREAKS: a guard that never fires, or fires on escaped and untagged prints, is noise
    """
    bare = tmp_path / "bare.py"
    bare.write_text('console.print(f"[red]x {e}[/red]")\n')
    escaped = tmp_path / "escaped.py"
    escaped.write_text('console.print(f"[red]x {escape(e)}[/red]")\n')
    plain = tmp_path / "plain.py"
    plain.write_text('console.print(f"plain {e}")\n')

    violations = scan(tmp_path)

    assert [(v.path, v.line, v.expression) for v in violations] == [(bare, 1, "e")]
