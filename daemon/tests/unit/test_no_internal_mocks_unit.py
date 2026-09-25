"""No test patches prismis's own code (constitution Principle I).

Invariant protected:
  - no test in daemon/tests or cli/tests replaces anything under prismis_daemon or cli
    through patch(), patch.object(), monkeypatch.setattr() or attribute assignment,
    except the LLM provider boundary and module-level constants

The scan reads each test file's syntax tree, so it catches the patch however it is
spelled, and it fails naming the file, line and target. What it cannot see: a
hand-written stand-in passed through a constructor argument. Those are caught by
review, not by this test.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_TEST_ROOTS = (_REPO / "daemon" / "tests", _REPO / "cli" / "tests")
_OWN_PACKAGES = ("prismis_daemon", "cli")

# The LLM provider is the one boundary Principle I permits faking.
_ALLOWED_ATTRS = frozenset({"complete", "health_check"})

# Call-through spies that only count calls to the real object (work order outOfScope).
_EXEMPT_FILES = frozenset({"test_api_connection_cleanup.py"})

_FAKE_FACTORIES = frozenset({"Mock", "MagicMock", "AsyncMock", "create_autospec"})


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.target}"


def _is_own(dotted: str) -> bool:
    return dotted.split(".")[0] in _OWN_PACKAGES


def _allowed(dotted: str) -> bool:
    last = dotted.rsplit(".", 1)[-1]
    return last in _ALLOWED_ATTRS or last.isupper()


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.own_names: set[str] = set()
        self.local_functions: set[str] = set()
        self.violations: list[Violation] = []

    def _flag(self, node: ast.AST, target: str) -> None:
        if not _allowed(target):
            self.violations.append(
                Violation(self.path, getattr(node, "lineno", 0), target)
            )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_own(alias.name):
                self.own_names.add((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and _is_own(node.module) and node.level == 0:
            for alias in node.names:
                self.own_names.add(alias.asname or alias.name)

    def _own_root(self, node: ast.expr) -> str | None:
        """The dotted name of node when it is rooted in something from our packages."""
        dotted = _dotted(node)
        if dotted and dotted.split(".")[0] in self.own_names:
            return dotted
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # A fixture's name, used in a test, is its return value, not the function.
        is_fixture = any(
            (_dotted(d.func if isinstance(d, ast.Call) else d) or "").endswith(
                "fixture"
            )
            for d in node.decorator_list
        )
        if not is_fixture:
            self.local_functions.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # `storage = Storage(...)`: the variable now names one of our objects.
        if isinstance(node.value, ast.Call) and self._own_root(node.value.func):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.own_names.add(target.id)
        # `notifier._send = fake`: a function replacing a method on one of our objects.
        # Assigning an object (`app.state.deep_extractor = extractor`) is injection, not a fake.
        value = node.value
        replaces_behavior = (
            isinstance(value, ast.Lambda)
            or (isinstance(value, ast.Name) and value.id in self.local_functions)
            or (
                isinstance(value, ast.Call)
                and (_dotted(value.func) or "").rsplit(".", 1)[-1] in _FAKE_FACTORIES
            )
        )
        if replaces_behavior:
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    root = self._own_root(target.value)
                    if root:
                        self._flag(node, f"{root}.{target.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = _dotted(node.func) or ""
        name = func.rsplit(".", 1)[-1]
        args = node.args
        if name == "patch" and args and isinstance(args[0], ast.Constant):
            target = str(args[0].value)
            if _is_own(target):
                self._flag(node, target)
        elif (func.endswith("patch.object") or name == "setattr") and args:
            first = args[0]
            # monkeypatch.setattr("pkg.mod.x", v) names its target as one string.
            if isinstance(first, ast.Constant) and _is_own(str(first.value)):
                self._flag(node, str(first.value))
            elif len(args) >= 2 and isinstance(args[1], ast.Constant):
                root = self._own_root(first)
                if root:
                    self._flag(node, f"{root}.{args[1].value!s}")
        self.generic_visit(node)


def scan(path: Path) -> list[Violation]:
    scanner = _Scanner(path)
    scanner.visit(ast.parse(path.read_text(), filename=str(path)))
    return scanner.violations


def _test_files() -> list[Path]:
    return sorted(
        p
        for root in _TEST_ROOTS
        for p in root.rglob("*.py")
        if p.name not in _EXEMPT_FILES and ".venv" not in p.parts
    )


def test_no_test_patches_prismis_internals() -> None:
    """
    INVARIANT: No test file patches prismis_daemon or cli internals
    BREAKS: A test proves a fake instead of prismis, and the honor-system marker that
            let internal patches through (claudex-guard: allow-mock) is back
    """
    files = _test_files()
    assert len(files) > 50, (
        f"the scan found only {len(files)} test files; the roots moved"
    )

    violations = [v for f in files for v in scan(f)]

    assert not violations, "internal code patched in tests:\n" + "\n".join(
        str(v) for v in violations
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (
            "from unittest.mock import patch\n"
            'def t():\n    with patch("prismis_daemon.storage.get_db_connection"):\n        pass\n',
            "prismis_daemon.storage.get_db_connection",
        ),
        (
            "from unittest.mock import patch\nfrom prismis_daemon.storage import Storage\n"
            'def t():\n    with patch.object(Storage, "get_active_sources"):\n        pass\n',
            "Storage.get_active_sources",
        ),
        (
            "from prismis_daemon.notifier import Notifier\n"
            "def t():\n    n = Notifier({})\n    n._send_notification = lambda items: None\n",
            "n._send_notification",
        ),
        (
            'def t(monkeypatch):\n    monkeypatch.setattr("cli.extract.APIClient", object)\n',
            "cli.extract.APIClient",
        ),
    ],
    ids=["patch-string", "patch-object", "attribute-lambda", "monkeypatch-string"],
)
def test_guard_detects_a_planted_internal_patch(
    source: str, target: str, tmp_path: Path
) -> None:
    """
    INVARIANT: Each way of patching an internal is reported with its target
    BREAKS: The tree scan passes because the scanner cannot see the violation, not
            because there is none
    """
    planted = tmp_path / "test_planted.py"
    planted.write_text(source)

    assert [v.target for v in scan(planted)] == [target]


def test_guard_detects_nothing_in_an_allowlisted_llm_patch(tmp_path: Path) -> None:
    """
    INVARIANT: The LLM provider boundary and module constants stay patchable
    BREAKS: The guard forbids the one fake the constitution permits
    """
    planted = tmp_path / "test_planted.py"
    planted.write_text(
        "from unittest.mock import patch\n"
        "import prismis_daemon.api as api\n"
        "def t(monkeypatch):\n"
        '    with patch("prismis_daemon.summarizer.complete"):\n'
        "        pass\n"
        '    with patch("prismis_daemon.llm_validator.llm_core.health_check"):\n'
        "        pass\n"
        '    monkeypatch.setattr(api, "SOURCE_VALIDATION_TIMEOUT", 0.05)\n'
    )

    assert scan(planted) == []
