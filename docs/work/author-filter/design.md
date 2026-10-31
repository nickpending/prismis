---
type: design
date: 2026-10-31
title: "Design: author-filter"
description: "The operator can narrow a semantic search to one author's writing. RSS items now store who wrote them, in the same analysis.metrics.author key Reddit posts already fill. That is the entry's own authors, or, for an Atom entry that names none, the feed's authors. /api/search and `prismis-cli search` take an `author` filter, a case-insensitive substring over that key, which narrows the KNN candidate set itself the way `source` and `kind` do and composes with both."
purpose: "The recorded design of the author-filter work order, read by its plan and its builders."
producer: cli:shape
---

# Design: author-filter

## Purpose

The operator can narrow a semantic search to one author's writing. RSS items now store who wrote them, in the same analysis.metrics.author key Reddit posts already fill. That is the entry's own authors, or, for an Atom entry that names none, the feed's authors. /api/search and `prismis-cli search` take an `author` filter, a case-insensitive substring over that key, which narrows the KNN candidate set itself the way `source` and `kind` do and composes with both.

## Form

1. Capture (daemon/src/prismis_daemon/fetchers/rss.py). One module-level helper, `_entry_author(entry, feed) -> str | None`, where `feed` is the parsed result the fetch loop already holds. It reads feedparser's `entry.get("authors")` list, never `entry.author`, which feedparser sets to the last of several authors (docs/work/author-filter/shape/feedparser-authors.txt). For each dict it takes the stripped `name`, or the stripped `email` when there is no name, and drops empty values. When the entry yields no name and `feed.version` starts with "atom", it reads `feed.feed.get("authors")` the same way, because RFC 4287 section 4.2.1 makes an Atom entry without its own author inherit the feed's, and feedparser does not copy it down. simonwillison.net/atom/everything/, #37's own example, declares its author only there (docs/work/author-filter/shape/cassette-feed-authors.txt). An RSS entry never inherits: feedparser fills an RSS feed's `feed.authors` from managingEditor, which names an editor, not each item's author. The names are joined with ", ", and the helper returns None when none are left. feedparser has already split the RSS 2.0 `email (Name)` form into name='Jane Doe', so the stored value is the display name with no parsing of our own. Where the RSS fetcher builds each ContentItem (the `item = ContentItem(` block in fetch_content), the analysis dict gains `"metrics": {"author": author}` when the helper returns a value and gains nothing otherwise. The existing `or None` keeps an entry with no author, fetch outcome or comments outcome at analysis=None. Nothing else changes: `_merge_analysis` (orchestrator.py:718) already carries a fetcher's metrics into the stored row. The summarizer already reads metrics as its metadata (orchestrator.py:162) and writes an `Author:` line for it (summarizer.py:507), as it does for Reddit today. New items only; stored rows are not backfilled.

2. Filter (daemon/src/prismis_daemon/storage.py, `search_content`). The method gains `author_filter: str | None = None` beside `source_filter`. The guard that adds the candidate subquery becomes `if source_filter or kind_filter or author_filter`. When an author is given, the subquery gains `AND LOWER(json_extract(c.analysis, '$.metrics.author')) LIKE '%' || LOWER(?) || '%'`, parameterized like the source condition at storage.py:2060. An item with no stored author has a NULL there and never matches. The docstring names the new argument. The unfiltered path is untouched.

3. API (daemon/src/prismis_daemon/api.py, `semantic_search`). It gains `author: str | None = Query(None, description="Filter by author (case-insensitive substring match)")`, passes `author_filter=author` to `search_content`, adds `"author": author` to `filters_applied`, and names `author` in the docstring's Args. The parameter is optional, so an older CLI that never sends it gets today's behaviour.

4. CLI (cli/src/cli/search.py, cli/src/cli/api_client.py). `search` gains `author: str = typer.Option(None, "--author", "-a", help="Filter by author (case-insensitive substring)")` and passes it to `APIClient.search(author=...)`. That method puts `author` on the params only when it is truthy, the same guard `source` uses. The table and `--json` output are otherwise unchanged.

5. Docs. API.md's Semantic Search section lists `author` after `source`, says which sources carry an author (Reddit posts, and RSS items fetched from now on), and adds `"author": null` to the filters_applied example. README's CLI example block gains `prismis-cli search "local-first databases" --author "simon willison"`. In docs/architecture/components.md, the Fetchers entry says the RSS fetcher stores analysis.metrics.author, and the API Server entry lists `author` among search's filters. docs/architecture/boundaries.md's kind-filter contract stays as it is.

Acts on the gap: The RSS fetcher writes each entry's author into analysis.metrics.author, the key Reddit already fills. For an Atom entry with none of its own, that is the feed's author. search_content's KNN candidate subquery gains one parameterized substring condition on that key, which /api/search exposes as `author` and `prismis-cli search` as `--author`. The search path therefore now reads an author, and the RSS items the issue is about, Atom feeds that name their author at feed level included, now have one to read.

## Commitments

- A real local HTTP server serves an RSS 2.0 feed and an Atom feed. They are fetched through the real RSSFetcher and stored through the real DaemonOrchestrator.analyze_and_store_item, with only the LLM provider stood in. The RSS feed has a dc:creator entry 'Simon Willison', an <author>jane@example.com (Jane Doe)</author> entry, an entry with two dc:creator values 'A One' and 'B Two', and an entry with no author under a channel <managingEditor>ed@example.com (Ed Itor)</managingEditor>. The Atom feed declares <author><name>Feed Writer</name></author> at feed level only and has one entry with no author of its own. The stored rows carry analysis.metrics.author 'Simon Willison', 'Jane Doe', 'A One, B Two' and, for the Atom entry, 'Feed Writer'. The RSS entry under the managingEditor has no metrics.author.
- Over those same rows, GET /api/search?q=...&author=jane doe through the real app and real sqlite-vec database returns only Jane Doe's item. author=b two returns only the two-author item, author=SIMON only Simon Willison's, author=feed writer only the Atom entry, and author=ed itor nothing.
- An item whose metrics.author matches lies outside the top-100 nearest neighbours of the unfiltered query. It is still returned when `author` is given.
- `author` together with `source`, or with `kind`, returns only items that satisfy every given filter. An item that matches the author under a different source or kind is excluded.
- Without `author`, /api/search returns the same items as before the change. Items with no stored author are excluded only when `author` is given. filters_applied reports the author value, or null when none is given.
- `prismis-cli search <q> --author <name>` against a real running daemon (the CLI suite's live_daemon) shows only that author's items, in both the table and the --json output, and APIClient.search sends no `author` param when none is given.
- API.md, README.md and docs/architecture/components.md describe the filter and where authors come from, as the form says.
- ember verify passes.

## Sacrifices

- RSS rows stored before this change carry no author and are never matched by the author filter. No backfill runs; a row gains an author only if it is stored again. (19963 of 41863 every content row on the operator's daemon host, 2026-10-31)
- YouTube and file items carry no author, so the author filter never matches them. A YouTube channel is reached through `--source`, since a YouTube source's name is its channel. (1557 of 41863 every content row on the operator's daemon host, 2026-10-31 (1,073 youtube + 484 file))
- An RSS item whose feed names no author on its entries gets none, even when the RSS channel names a managingEditor or a channel-level creator, so the author filter never matches it; Hacker News's RSS is one such feed. (12332 of 19963 stored RSS rows on the operator's daemon host, by their source's feed class, 2026-10-31 (16 of 29 sources; Hacker News alone 11,625 rows))
- Case folding is ASCII-only, as SQLite's LOWER is for the source filter: an author name's non-ASCII letters match only in the case typed. (0 of 20343 every stored author on the operator's daemon host, 2026-10-31)

## Risk

If the capture writes the author somewhere other than analysis.metrics.author, or `_merge_analysis` stops carrying metrics, the filter silently matches nothing for RSS items. The first commitment's proof goes through the fetcher and the orchestrator's own store path, never a seeded row, so that break turns it red. LIKE treats `%` and `_` in the operator's input as wildcards, exactly as the source filter does today; the value is bound as a parameter, so nothing can be injected. RSS items with an author now send an `Author:` line to the summarizer, which changes the LLM input for them the way Reddit's already does. The one recorded-LLM test that runs the RSS fetcher reads a stub feed with no author, so its recording is unaffected. If an Atom feed's entries name no author and its feed-level author is a publication rather than a person, every entry carries that name, which is what the Atom feed itself declares.

## Licensed by

daemon/src/prismis_daemon/storage.py:2060
