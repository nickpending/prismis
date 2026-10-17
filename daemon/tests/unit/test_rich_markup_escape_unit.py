"""Exception text printed through Rich must go through `rich.markup.escape`.

Config errors name the section to fix in square brackets, for example "Add [api]
section to config.toml". Interpolated into markup unescaped, `[api]` is parsed as a style
tag and dropped, so the hint disappears exactly when it is needed, and text such as
`[/x]` makes Rich raise `MarkupError` inside the error handler itself.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ERROR_NAMES = {"e", "exc", "err", "error", "message", "msg", "reason"}


def _is_error_text(node: ast.expr) -> bool:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
    ):
        node = node.args[0]
    return isinstance(node, ast.Name) and node.id in ERROR_NAMES


def _unescaped_error_prints(source: Path, root: Path) -> list[str]:
    found = []
    for call in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "print"
        ):
            continue
        for arg in call.args:
            if isinstance(arg, ast.JoinedStr) and any(
                isinstance(part, ast.FormattedValue) and _is_error_text(part.value)
                for part in ast.walk(arg)
            ):
                found.append(f"{source.relative_to(root)}:{call.lineno}")
    return found


def test_no_print_interpolates_unescaped_error_text():
    offenders = [
        site
        for source in sorted(SRC.rglob("*.py"))
        for site in _unescaped_error_prints(source, SRC.parent)
    ]
    assert offenders == [], (
        "wrap error text in rich.markup.escape(str(...)) before putting it in markup: "
        + ", ".join(offenders)
    )


def test_guard_flags_unescaped_and_accepts_escaped_error_text(tmp_path):
    flagged = tmp_path / "flagged.py"
    flagged.write_text(
        "def f(console, e):\n"
        '    console.print(f"[red]Error: {e}[/red]")\n'
        '    console.print(f"[red]Error: {str(e)}[/red]")\n'
        '    console.print(f"[red]Error: {escape(str(e))}[/red]")\n',
        encoding="utf-8",
    )
    sites = _unescaped_error_prints(flagged, tmp_path)
    assert [site.rsplit(":", 1)[1] for site in sites] == ["2", "3"]
