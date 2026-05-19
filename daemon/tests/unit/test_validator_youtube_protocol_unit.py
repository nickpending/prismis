"""Regression test for gh #27 — _validate_youtube returned 4-tuple on youtube:// branch.

Callers at api.py:405 and api.py:503 destructure validate_source() as a 3-tuple. The
youtube:// protocol path previously returned 4 values, raising ValueError on unpack
for any direct API call with youtube://@handle or youtube://UC... URLs (CLI rewrites
the protocol client-side so CLI users were unaffected — TUI/web/curl direct callers
hit the 500).
"""

import pytest

from prismis_daemon.validator import SourceValidator


@pytest.mark.parametrize(
    "url",
    [
        "youtube://@mkbhd",
        "youtube://UCsBjURrPoezykLs9EqgamOA",
    ],
)
def test_validate_source_youtube_protocol_returns_3_tuple(url: str) -> None:
    is_valid, error_msg, metadata = SourceValidator().validate_source(url, "youtube")
    assert is_valid is True
    assert error_msg is None
    assert metadata is None
