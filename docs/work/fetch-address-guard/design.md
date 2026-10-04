---
type: design
date: 2026-10-04
title: "Design: fetch-address-guard"
description: "Untrusted article links cannot make cerebro fetch pages on its own machine or the home network: every connection `extract_article` opens is checked against the address it actually connects to, before the request is sent and after every redirect."
purpose: "The recorded design of the fetch-address-guard work order, read by its plan and its builders."
producer: cli:shape
---

# Design: fetch-address-guard

## Purpose

Untrusted article links cannot make cerebro fetch pages on its own machine or the home network: every connection `extract_article` opens is checked against the address it actually connects to, before the request is sent and after every redirect.

## Form

Check the address at the moment of connection, inside the opener the extractor already builds.

1. `daemon/src/prismis_daemon/article_extractor.py`: `_http_only_opener` (line 44) installs HTTP and HTTPS handlers whose connection classes replace `_create_connection` with a guarded one. It resolves the host once with `socket.getaddrinfo`, refuses unless every returned address is public (`ipaddress.ip_address(...).is_global`, which covers loopback, private, link-local including 169.254.169.254, carrier-grade NAT, unique-local IPv6 and IPv4-mapped forms), and connects to the first returned address, so the address checked is the address used. A refused address raises a dedicated `BlockedAddressError`, which `extract_article` turns into `ArticleResult(None, "fetch_failed", "blocked address")`. Redirects open new connections through the same classes, so each hop is checked with no redirect-specific code. HTTPS keeps the URL's hostname for SNI and certificate checks, since only the TCP connect target changes.

2. `extract_article(url, *, allowed_private_hosts=())` skips the check only for a hostname listed exactly in `allowed_private_hosts`. `daemon/src/prismis_daemon/config.py` gains `fetch_allow_private_hosts: list[str]`, read as `daemon.get("fetch_allow_private_hosts", [])` so every existing config.toml still loads; `daemon/src/prismis_daemon/defaults.py` documents it, commented out, in the `[daemon]` template. fetchers/rss.py (line 267), fetchers/reddit.py (line 449) and refetch.py (line 194) pass their config's list.

3. The test suite's materialized config (daemon/tests/conftest.py `isolated_xdg_env`) sets `fetch_allow_private_hosts = ["127.0.0.1"]`, so tests serving pages from the loopback server keep working through the real fetchers.

4. Tests: `extract_article` against a local server with an empty allow list returns `fetch_failed` / `blocked address` for `http://127.0.0.1:<port>/` and `http://localhost:<port>/`, and makes no request (the server records none); a public-looking hostname that redirects to `http://127.0.0.1:<port>/` is refused at the redirect; with `127.0.0.1` allowed the same page is fetched; 10.0.0.1, 192.168.1.1, 169.254.169.254, `::1` and `::ffff:127.0.0.1` are refused by the address check; a config.toml without the new key loads with an empty list. Each shown red with the guard removed.

5. `docs/architecture/boundaries.md` gains, beside the title-only contract: article fetches refuse non-public addresses at connect time, except hosts named in `fetch_allow_private_hosts`.

## Commitments

- `extract_article` never connects to a loopback, private, link-local or otherwise non-public address unless its hostname is named in `fetch_allow_private_hosts`.
- The address checked is the address connected to, and every redirect hop is checked.
- A refused fetch is recorded as `fetch_failed` with detail `blocked address`.
- The only way to allow a private host is naming it in config.toml; no environment variable or default allows one.
- Existing config.toml files load unchanged.

## Sacrifices

- A feed whose entries link to pages on the operator's own network yields title-only items until those hosts are added to `fetch_allow_private_hosts`; no current source does.
- A request through an HTTP proxy is checked against the proxy's address, not the final site; cerebro uses no proxy.

## Risk

If `is_global` classifies an address the operator considers safe as non-public, that page becomes title-only with reason `fetch_failed:blocked address`, visible in the stored reason; if a new caller fetches article URLs without going through `extract_article`, it is unguarded.

## Licensed by

daemon/src/prismis_daemon/article_extractor.py:44
