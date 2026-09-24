"""The locked daemon resolves no CUDA build of torch (#74).

Invariant protected:
  - uv.lock contains no nvidia-* package, and Linux torch comes from the CPU index

PyPI's default Linux torch wheel pulls ~4GB of nvidia CUDA libraries onto hosts with
no GPU. The pyproject routes torch to the CPU index; this reads the resolution that
routing produces, so dropping the source or the direct torch dependency turns it red.
"""

import tomllib
from pathlib import Path

_LOCK = Path(__file__).parents[2] / "uv.lock"


def test_lock_resolves_no_cuda_packages() -> None:
    """
    INVARIANT: No nvidia-* package and no CUDA torch is in the daemon's resolution
    BREAKS: Every Linux install carries ~4GB of GPU libraries that cannot run there
    """
    packages = tomllib.loads(_LOCK.read_text())["package"]

    nvidia = sorted(p["name"] for p in packages if p["name"].startswith("nvidia-"))
    assert not nvidia, f"CUDA libraries in the lock: {nvidia}"

    torch_sources = {
        p["version"]: p["source"].get("registry")
        for p in packages
        if p["name"] == "torch"
    }
    linux = [v for v in torch_sources if v.endswith("+cpu")]
    assert linux, f"no CPU torch build is locked: {torch_sources}"
    assert torch_sources[linux[0]] == "https://download.pytorch.org/whl/cpu"
