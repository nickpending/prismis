"""Run the `make release` shell proof inside the gate.

scripts/tests/test_make_release.sh drives the real Makefile target and release script in
throwaway clones of a local bare remote. Collecting it here is what makes it a standing
check: verify.sh discovers this pytest unit, and nothing else would run the script.
"""

import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "tests" / "test_make_release.sh"
)


def test_make_release_shell_proof_passes() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith("make release test: PASS")
