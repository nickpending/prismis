"""Structural unit tests for @typing.overload stubs on _rfc3339 (task 2.14).

SC-37: Three @overload decorators must be present immediately before the
       _rfc3339 implementation, in narrowest-to-widest signature order.

Protects the annotation-only invariant that:
- The overload stubs actually exist (not silently reverted by a future edit)
- The stubs appear in the correct narrowest-to-widest order
  (datetime->str, None->None, datetime|None->str|None)
- `overload` is imported from `typing` (required for stubs to function)

These tests are structural (source-text inspection via AST + grep patterns),
not runtime — the stubs have no runtime effect but their presence enables
type-narrowing for future non-Optional field serializers that delegate to _rfc3339.
"""

import ast
from pathlib import Path

# Derive api_models.py location from this test file's own path:
#   __file__  → daemon/tests/unit/test_rfc3339_overload_stubs_unit.py
#   parents[0] → daemon/tests/unit/
#   parents[1] → daemon/tests/
#   parents[2] → daemon/
API_MODELS_PATH = Path(__file__).parents[2] / "src" / "prismis_daemon" / "api_models.py"


def _parse_module() -> ast.Module:
    """Parse api_models.py into an AST. Fails loudly if file not found."""
    assert API_MODELS_PATH.exists(), f"api_models.py not found at {API_MODELS_PATH}"
    source = API_MODELS_PATH.read_text(encoding="utf-8")
    return ast.parse(source)


def _get_rfc3339_group(tree: ast.Module) -> list[ast.stmt]:
    """Return the consecutive block of _rfc3339 function defs (stubs + impl).

    All top-level FunctionDef nodes named '_rfc3339' appear consecutively:
    three @overload stubs followed by the implementation (no @overload).
    """
    stmts = tree.body
    group = [
        node
        for node in stmts
        if isinstance(node, ast.FunctionDef) and node.name == "_rfc3339"
    ]
    assert len(group) > 0, "_rfc3339 function not found in api_models.py"
    return group


# ---------------------------------------------------------------------------
# INV-OL-1: exactly 3 @overload decorators present on _rfc3339
# ---------------------------------------------------------------------------


def test_rfc3339_has_three_overload_stubs() -> None:
    """SC-37 / INV-OL-1: three @overload stubs must be present on _rfc3339.

    If stubs are missing (e.g., reverted by a future edit), type-checkers cannot
    narrow _rfc3339(datetime) to str — every future non-Optional @field_serializer
    would need a hand-annotated callsite return type, reintroducing the symptom
    that task 2.14 was built to eliminate.
    """
    source = API_MODELS_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()

    overload_count = sum(1 for line in lines if line.strip() == "@overload")

    assert overload_count >= 3, (
        f"SC-37: expected at least 3 @overload decorators in api_models.py, "
        f"found {overload_count}. "
        "Three stubs are required for _rfc3339 type-narrowing: "
        "(datetime)->str, (None)->None, (datetime|None)->str|None."
    )


# ---------------------------------------------------------------------------
# INV-OL-2: stubs appear in narrowest-to-widest order
# ---------------------------------------------------------------------------


def test_rfc3339_overload_stub_ordering() -> None:
    """SC-37 / INV-OL-2: stubs must appear narrowest-to-widest before the impl.

    Required order per Python @overload resolution (first-match-wins):
      1. (datetime) -> str          — most specific, non-Optional
      2. (None) -> None             — explicit None case
      3. (datetime | None) -> str | None  — widest (catch-all for combined types)
      4. implementation (no @overload)

    Wrong ordering breaks type-narrowing silently: (datetime|None)->str|None first
    would shadow the narrower stubs and return str|None for datetime inputs.
    """
    tree = _parse_module()
    group = _get_rfc3339_group(tree)

    # Must have at least 4 entries: 3 stubs + 1 impl
    assert len(group) >= 4, (
        f"Expected at least 4 _rfc3339 defs (3 stubs + 1 impl), "
        f"found {len(group)}. Stubs may be missing."
    )

    stubs = group[:-1]  # All but the last (impl)
    impl = group[-1]

    # Impl must NOT have @overload
    impl_has_overload = any(
        (isinstance(d, ast.Name) and d.id == "overload")
        or (isinstance(d, ast.Attribute) and d.attr == "overload")
        for d in impl.decorator_list
    )
    assert not impl_has_overload, (
        "The _rfc3339 implementation must NOT have @overload decorator. "
        "Only the stubs should carry @overload."
    )

    # All stubs must have @overload
    for i, stub in enumerate(stubs):
        stub_has_overload = any(
            (isinstance(d, ast.Name) and d.id == "overload")
            or (isinstance(d, ast.Attribute) and d.attr == "overload")
            for d in stub.decorator_list
        )
        assert stub_has_overload, (
            f"Stub #{i + 1} (_rfc3339 at line {stub.lineno}) is missing @overload decorator."
        )

    # Check arg annotations for ordering (narrowest-to-widest):
    # Stub 0: arg annotation must not be a Union (pure `datetime`)
    # Stub 1: arg annotation must be None/NoneType
    # Stub 2: arg annotation must be a Union/BinOp
    def _arg_annotation(stub: ast.FunctionDef) -> ast.expr | None:
        args = stub.args.args
        # First arg is `v`; skip `self` if present
        v_arg = next((a for a in args if a.arg == "v"), None)
        return v_arg.annotation if v_arg else None

    ann0 = _arg_annotation(stubs[0])
    ann1 = _arg_annotation(stubs[1])
    ann2 = _arg_annotation(stubs[2])

    # Stub 0: v: datetime (Name node, not Union/BinOp)
    assert ann0 is not None, "Stub 0 must have type annotation on `v`"
    assert isinstance(ann0, ast.Name), (
        f"Stub 0 must be the narrowest: v: datetime (ast.Name), "
        f"got {type(ann0).__name__}. "
        "Expected: @overload def _rfc3339(v: datetime) -> str: ..."
    )
    assert ann0.id == "datetime", f"Stub 0 must be v: datetime, got v: {ann0.id}"

    # Stub 1: v: None (Constant node with value None)
    assert ann1 is not None, "Stub 1 must have type annotation on `v`"
    assert isinstance(ann1, ast.Constant) and ann1.value is None, (
        f"Stub 1 must be: v: None (ast.Constant None), "
        f"got {type(ann1).__name__}. "
        "Expected: @overload def _rfc3339(v: None) -> None: ..."
    )

    # Stub 2: v: datetime | None (BinOp with `|` operator in Python 3.10+ style)
    assert ann2 is not None, "Stub 2 must have type annotation on `v`"
    assert isinstance(ann2, ast.BinOp), (
        f"Stub 2 must be the widest: v: datetime | None (ast.BinOp), "
        f"got {type(ann2).__name__}. "
        "Expected: @overload def _rfc3339(v: datetime | None) -> str | None: ..."
    )


# ---------------------------------------------------------------------------
# INV-OL-3: `overload` is imported from `typing`
# ---------------------------------------------------------------------------


def test_rfc3339_overload_imported_from_typing() -> None:
    """INV-OL-3: `overload` must be imported from `typing` for stubs to function.

    Without this import, `@overload` is an undefined name at module load time,
    causing NameError at import and crashing the API server on startup.
    """
    tree = _parse_module()

    overload_imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name == "overload":
                    overload_imported = True
                    break
        if overload_imported:
            break

    assert overload_imported, (
        "INV-OL-3: `overload` must be imported from `typing` in api_models.py. "
        "Without this import, @overload decorators raise NameError at import time "
        "and crash the FastAPI server."
    )
