"""Run the `make test` shell proof inside the gate.

scripts/tests/test_make_test.sh drives the real Makefile target and macos/scripts/test.sh in
a throwaway tree of small real units. Collecting it here is what makes it a standing check:
ember verify and make test run this unit's pytest, and nothing else runs the script. The
proof's tree holds its own units, so the nested `make test` never reaches this test again.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "tests" / "test_make_test.sh"


def test_make_test_shell_proof_passes() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith("make test test: PASS")
