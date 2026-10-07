"""No file under cli/ suppresses the blind-except rule (gh #58).

Invariant protected:
  - no comment under cli/ carries a `noqa` (or file-level `ruff: noqa`) that names
    BLE001 or any other BLE code, and cli/pyproject.toml lists no BLE code under
    per-file-ignores or ignore

A blind `except Exception` is resolved one handler at a time: narrowed, kept as a
boundary that records its traceback, raised to a boundary that already reports
failure, or recorded on the item. A suppression comment turns that decision back into
silence, so the lint rule is only worth its gate while nothing can switch it off.

The scan reads comment tokens, so a `noqa` quoted inside a string or docstring is not
a comment and is not flagged. A bare `# noqa` is out of scope here: ruff's PGH004
rejects it.

Beside the ban, a broad handler in src/ must re-raise or log its traceback at WARNING or
above: ruff's BLE001 also accepts `exc_info=True` at debug level, which hides a real
defect's traceback from anyone reading the default log.
"""

import ast
import io
import re
import tokenize
import tomllib
from pathlib import Path

_UNIT_ROOT = Path(__file__).resolve().parents[2]

_NOQA = re.compile(
    r"#\s*(?:ruff:\s*)?noqa\s*:\s*(?P<codes>[A-Za-z0-9]+(?:[\s,]+[A-Za-z0-9]+)*)",
    re.IGNORECASE,
)


def find_blind_noqa(root: Path) -> list[str]:
    """Every `file:line` under root whose comment suppresses a BLE code."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            match = _NOQA.search(token.string)
            if not match:
                continue
            codes = re.split(r"[\s,]+", match.group("codes"))
            if any(code.upper().startswith("BLE") for code in codes):
                found.append(f"{path.relative_to(root)}:{token.start[0]}")
    return found


def find_blind_pyproject_ignores(pyproject: Path) -> list[str]:
    """Every BLE code listed in a ruff ignore or per-file-ignore of pyproject."""
    lint = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]
    listed = list(lint.get("ignore", []))
    for codes in lint.get("per-file-ignores", {}).values():
        listed.extend(codes)
    return [code for code in listed if code.upper().startswith("BLE")]


_BROAD = frozenset({"Exception", "BaseException"})
_LOG_AT_WARNING_OR_ABOVE = frozenset({"warning", "warn", "error", "critical"})


def _is_broad(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if caught is None:
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(isinstance(n, ast.Name) and n.id in _BROAD for n in names)


def _records_its_failure(handler: ast.ExceptHandler) -> bool:
    """Whether the handler re-raises, or logs its traceback at WARNING or above."""
    for node in (n for stmt in handler.body for n in ast.walk(stmt)):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "exception":
                return True
            keeps_traceback = any(
                kw.arg == "exc_info"
                and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                for kw in node.keywords
            )
            if node.func.attr in _LOG_AT_WARNING_OR_ABOVE and keeps_traceback:
                return True
    return False


def find_unrecorded_broad_handlers(src: Path) -> list[str]:
    """Every `file:line` of a broad handler that neither re-raises nor logs a traceback.

    A boundary that logs at debug with exc_info satisfies ruff's BLE001 and hides the
    traceback from anyone reading at the default level; this is the check ruff cannot make.
    """
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node):
                if not _records_its_failure(node):
                    found.append(f"{path.relative_to(src)}:{node.lineno}")
    return found


def test_no_blind_except_suppression_comment_under_cli() -> None:
    """
    INVARIANT: no `# noqa: BLE001` (or any BLE code) exists under cli/
    BREAKS: a handler is silenced instead of resolved, and `ruff check .` stays green
    """
    found = find_blind_noqa(_UNIT_ROOT)
    assert found == [], (
        f"BLE suppression comment at {found}; narrow the handler or record its "
        "traceback instead of silencing the rule"
    )


def test_pyproject_lists_no_blind_except_ignore() -> None:
    """
    INVARIANT: cli/pyproject.toml suppresses BLE001 for no file
    BREAKS: the blanket per-file-ignore returns and the rule reports nothing again
    """
    assert find_blind_pyproject_ignores(_UNIT_ROOT / "pyproject.toml") == []


def test_scanner_names_file_and_line_of_a_planted_suppression(tmp_path: Path) -> None:
    """
    INVARIANT: a planted BLE suppression is reported with its file and line
    BREAKS: the ban test passes on a tree that contains the suppression it forbids
    """
    planted = tmp_path / "pkg" / "planted.py"
    planted.parent.mkdir()
    planted.write_text(
        "try:\n"
        "    pass\n"
        "except Exception:  # noqa: BLE001\n"
        "    pass\n"
        "try:\n"
        "    pass\n"
        "except Exception:  # noqa: S110, BLE\n"
        "    pass\n"
        "# ruff: noqa: BLE001\n"
    )

    assert find_blind_noqa(tmp_path) == [
        "pkg/planted.py:3",
        "pkg/planted.py:7",
        "pkg/planted.py:9",
    ]

    planted.write_text("try:\n    pass\nexcept ValueError:\n    pass\n")
    assert find_blind_noqa(tmp_path) == [], "the ban passes again once it is removed"


def test_scanner_ignores_other_codes_and_quoted_text(tmp_path: Path) -> None:
    """
    INVARIANT: only a BLE code in a real comment is flagged
    BREAKS: the ban fires on S110 noqas or on this very docstring and gets deleted
    """
    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""A docstring that quotes # noqa: BLE001 is not a comment."""\n'
        "value = 1  # noqa: E501\n"
        'text = "# noqa: BLE001"\n'
    )

    assert find_blind_noqa(tmp_path) == []


def test_every_broad_handler_in_src_records_its_traceback() -> None:
    """
    INVARIANT: every `except Exception` in src re-raises or logs a traceback at WARNING+
    BREAKS: a boundary logs at debug, or logs only the message, and the traceback of a
            real defect is invisible at the level the daemon runs at
    """
    found = find_unrecorded_broad_handlers(_UNIT_ROOT / "src")
    assert found == [], f"broad handler without a traceback at {found}"


def test_handler_scanner_flags_debug_and_message_only_handlers(tmp_path: Path) -> None:
    """
    INVARIANT: the traceback check fails on a handler that hides the traceback
    BREAKS: the check passes on a boundary that logs at debug, so it guards nothing
    """
    module = tmp_path / "module.py"
    module.write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    logger.debug('x', exc_info=True)\n"
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    logger.warning(f'x {e}')\n"
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    logger.warning('x', exc_info=True)\n"
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    logger.exception('x')\n"
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    raise RuntimeError('x') from e\n"
        "try:\n"
        "    pass\n"
        "except ValueError:\n"
        "    pass\n"
    )

    assert find_unrecorded_broad_handlers(tmp_path) == ["module.py:5", "module.py:9"]
