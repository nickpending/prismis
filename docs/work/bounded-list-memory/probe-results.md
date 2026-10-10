# Memory probe results: bounded-list-memory

`daemon/scripts/memory_probe.py` runs `GET /api/entries?limit=10000` with `view=list` and
`view=full` through the real app, each in its own process, dropping the body as it is sent.

## Measured on a copy of cerebro's database

A copy of cerebro's prismis.db (769 MB on disk) on macOS arm64, with the probe from this change
run against both trees. 15.1 MB list body, 265.7 MB full body.

| Code | view=list peak RSS | view=full peak RSS | full minus list |
|---|---|---|---|
| Before (main `0b27fdf`) | 637.0 MB | 2,731.2 MB | 2,094.2 MB (FAIL) |
| After (streamed) | 486.4 MB | 519.0 MB | 32.6 MB (PASS, limit 100 MB) |

Baseline RSS before the request was about 475 MB in every run (imports).

## Measured on a synthetic database

These figures are from a generated database of 10,000 non-archived rows, each with about 30 KB of content and an
analysis holding a 30 KB `full_text` (699 MB on disk, 653.5 MB full response body, 6.2 MB
list body), on macOS arm64. Absolute peaks differ from cerebro's; the full-minus-list gap is
what the criterion bounds.

| Code | view=list peak RSS | view=full peak RSS | full minus list |
|---|---|---|---|
| Before (HEAD `0b27fdf`) | 554.8 MB | 3,257.2 MB | 2,702.4 MB (FAIL) |
| After (streamed) | 490.9 MB | 496.1 MB | 5.2 MB (PASS, limit 100 MB) |

Baseline RSS before the request was about 475 MB in every run (imports).
