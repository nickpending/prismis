---
type: design
date: 2026-10-07
title: "Design: recorded-youtube-tests"
description: "Every daemon test that today skips for want of a live third party runs in every gate run against what that third party really returned, recorded once, while the six provider-behavior tests and the macOS notifier test stay opt-in. On 2026-10-07 the operator widened this work order from the content-aware and YouTube tests to all 32 live-resource-gated tests and chose vcrpy for HTTP."
purpose: "The recorded design of the recorded-youtube-tests work order, read by its plan and its builders."
producer: cli:shape
---

# Design: recorded-youtube-tests

## Purpose

Every daemon test that today skips for want of a live third party runs in every gate run against what that third party really returned, recorded once, while the six provider-behavior tests and the macOS notifier test stay opt-in. On 2026-10-07 the operator widened this work order from the content-aware and YouTube tests to all 32 live-resource-gated tests and chose vcrpy for HTTP.

## Form

Three record-and-replay boundaries, one per kind of third party, each strict on replay, plus local stand-ins for failure modes a recording cannot reproduce.

1. LLM (3 tests): test_content_aware_summarization_integration.py drops its OPENAI_API_KEY marks and takes the existing `recorded_llm` marker; recordings made with openai/gpt-5.4-nano; assertions kept.

2. yt-dlp (8 tests in test_youtube_fetcher_integration.py): a recorder/replayer script set as `YouTubeFetcher.yt_dlp_cmd` by a fixture. Record mode (PRISMIS_RECORD_YTDLP=1) runs the real `python -m yt_dlp` and saves each call's arguments, stdout, stderr, exit code and the subtitle files it wrote under daemon/tests/fixtures/ytdlp_recordings/; replay compares arguments after normalizing YYYYMMDD dates and the per-call temporary output path, writes the recorded files into the requested output directory and returns the recorded output; a missing or mismatched recording fails with the record command. The `if items:` and `if transcript:` guards become unconditional assertions.

3. HTTP (the content-fetching tests in test_validator_integration.py, test_fetcher_integration.py, test_api_integration.py and test_reddit_fetcher_integration.py): vcrpy, added to the daemon's dev dependency group (3.0k stars, release July 2026). A shared fixture gives each test a cassette under daemon/tests/fixtures/http_cassettes/<module>/<test>.yaml with `record_mode='none'` by default (a request not in the cassette fails the test) and `'once'` when PRISMIS_RECORD_HTTP=1. Matching is on method, scheme, host, path and query. `filter_headers` removes Authorization, Cookie and Set-Cookie, and a `before_record_response` hook replaces the `access_token` value in Reddit's /api/v1/access_token response, so no credential or token is written.

4. Failure modes the third party produced on demand become local, deterministic servers or reserved names, not recordings: httpbin.org/delay/5 becomes a local server that sleeps past the validator's timeout, httpbin.org/status/429 a local server answering 429, the invalid-domain cases use the reserved `.invalid` TLD, and the 'non-RSS content' case serves an HTML page locally. Each keeps its assertion.

5. Reddit recordings are made on cerebro, the only machine holding the Reddit credentials: the job's daemon test tree is copied to ~/prismis-record on cerebro (never /tmp, which is RAM there), recorded with cerebro's .env loaded, every cassette is grepped on cerebro for the client id, the client secret and the string 'access_token": "' with a real value before anything is copied back, and only the cassettes return. The scratch directory is removed afterwards.

6. Tests that stay opt-in and unchanged: test_llm_client_live_integration.py (4), test_llm_startup_validation_integration.py (2), test_notifier_integration.py (1, a macOS binary). `docs/architecture/boundaries.md`'s test section names the three replay mechanisms, their record commands and the opt-in live runs.

7. Mechanism tests: yt-dlp replay passes under `no_network`, fails on a missing recording and on an argument mismatch, and tolerates only a date or temp-path difference; an HTTP test whose cassette lacks a request fails rather than reaching the network; a cassette written in record mode contains no Authorization header and no real access_token.

## Commitments

- All 32 live-resource-gated tests outside the provider-behavior and notifier tests run in the gate with no network.
- Each boundary replays strictly: a missing or changed request fails with the command to re-record.
- Failure modes (timeouts, status codes, unresolvable hosts, non-feed pages) are produced locally, not replayed.
- No credential or token is ever written to a recording; Reddit recordings are made on cerebro and checked there before they leave.
- vcrpy is a dev-only dependency; the LLM recorder from recorded-llm-tests stays as it is.
- The YouTube tests' conditional guards become unconditional.

## Sacrifices

- Third-party changes (a feed's format, Reddit's API shape, yt-dlp's output) are not seen until the recordings are refreshed with the record commands.
- Re-recording Reddit needs cerebro.
- Two record/replay mechanisms exist, the LLM stub and vcrpy, at two different boundaries: a configured service URL and arbitrary third-party URLs.

## Risk

If a scrub misses a credential shape, a token could be committed; the cerebro-side grep before copying and the mechanism test on a recorded cassette are the two guards. If vcrpy's patching misses a client path (the guarded urllib opener), that test fails on replay rather than reaching the network under `no_network`.

## Licensed by

daemon/tests/conftest.py:289
