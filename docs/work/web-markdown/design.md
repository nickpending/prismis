---
type: design
date: 2026-10-14
title: "Design: web-markdown"
description: "Render the web view's article detail as the markdown the summarizer writes, so the reading summary and the deep synthesis read as formatted text rather than raw ##, - and ** marks, without loading anything from a third-party host and without letting model output inject script into the page."
purpose: "The recorded design of the web-markdown work order, read by its plan and its builders."
producer: cli:shape
---

# Design: web-markdown

## Purpose

Render the web view's article detail as the markdown the summarizer writes, so the reading summary and the deep synthesis read as formatted text rather than raw ##, - and ** marks, without loading anything from a third-party host and without letting model output inject script into the page.

## Form

1. Vendored scripts. marked 18.0.14 (lib/marked.umd.js) and DOMPurify 3.4.16 (dist/purify.min.js), the newest releases at least two weeks old on 2026-10-14, are committed byte-for-byte from their npm tarballs into daemon/src/prismis_daemon/static/assets/ as marked.umd.js and purify.min.js. The directory is not named vendor because .gitignore:35 ignores vendor/, which would keep the files out of git and out of the wheel. Beside them, static/assets/VENDORED.json records for each file its package, version, npm tarball URL, license and sha256. The files are not edited, so their trailing sourceMappingURL comments stay and the .map files are not shipped.

2. Serving. api.py mounts /assets as StaticFiles over static/assets with check_dir=False, registered beside the /audio mount (api.py:1846) and before the SPA catch-all, and serve_spa (api.py:1856) adds assets/ to the prefixes it answers with 404 rather than index.html. The mount is unauthenticated, as index.html is. The request-logging middleware already skips /assets/ (api.py:109).

3. Loading. index.html loads /assets/marked.umd.js and /assets/purify.min.js with plain synchronous script tags placed before the inline application script (index.html:1054), so the marked and DOMPurify globals exist when the PrismisWebapp class runs.

4. Rendering. PrismisWebapp gains renderMarkdown(text, repeatedTitle): empty text returns an empty string; otherwise it lexes the text with marked.lexer (marked's defaults, GFM on), and when repeatedTitle is given and the first non-space token is a depth-1 heading whose trimmed text equals the trimmed repeatedTitle, it drops that token; it then returns DOMPurify.sanitize(marked.parser(tokens), MD_POLICY). The drop exists because 498 of the 500 newest reading summaries on cerebro open with '# <title>' copied verbatim from the article title the panel already shows as its link; any other h1 renders. MD_POLICY is an allowlist of exactly what markdown produces: ALLOWED_TAGS h1 to h6, p, br, hr, ul, ol, li, strong, em, del, code, pre, blockquote, a, table, thead, tbody, tr, th, td; ALLOWED_ATTR href, title, align. DOMPurify's default profile allows img, video, audio and svg, and any of those with a remote src would make the page fetch from a third-party host chosen by whoever wrote the article, so they are excluded rather than filtered; a markdown image renders as nothing. The constructor registers one DOMPurify afterSanitizeAttributes hook that gives every a element with an href target=_blank and rel='noopener noreferrer', matching the detail title link (index.html:2037). openDetail (index.html:2041) inserts renderMarkdown(summary, entry.title) into div.detail-summary and renderMarkdown(de.synthesis) into div.synthesis-body (index.html:2007); both divs gain the class md. renderSimpleMarkdown (index.html:2068) is deleted. There is no fallback to escaped text: if a script failed to load, openDetail's existing catch shows 'Failed to load:' with the error, and the browser tests catch a missing or unserved asset.

5. Styling. .detail-summary loses white-space: pre-wrap (index.html:795) and keeps overflow-wrap: anywhere. A .md rule block styles rendered markdown inside the panel: h1 to h4 at sizes below the 20px .detail-title, so a heading in the summary never outranks the panel title; ul and ol with left padding; li spacing; inline code and pre on a dark tint, pre with overflow-x: auto; a in the panel accent with underline on hover; blockquote with a left rule; table as display: block with overflow-x: auto. The synthesis heading rule moves from .detail-synthesis h4 to .synthesis-body h1 to h4, because marked renders ### as h3 and .detail-synthesis h3 is the card's own 'Deep Synthesis' label (index.html:807). The 768px block keeps its detail rules and adds nothing new unless the overflow tests show it must.

6. Tests. In test_web_browser_integration.py, seeded_ids gains three items. 'markdown' carries an analysis.reading_summary opening with a # heading whose text differs from the item's title, then ## headings, a - list, a numbered list, **bold**, inline `code` and a [link](https://example.com/md), and a deep_extraction.synthesis with ### headings and a - list. 'titled' carries a reading_summary opening with '# ' plus the item's exact title, then '## Overview' and a paragraph. 'hostile' carries a reading_summary with '## Safe heading', a safe [link](https://example.com/ok), a <script> that sets window.__xss, an <img src=x onerror> that sets window.__xss, a [link](javascript:...) that would set it, and a markdown image ![x](http://evil.example/p.png). Test one opens 'markdown' and asserts .detail-summary holds h1, h2, ul>li, ol>li, strong, code and an a with the seeded href, target=_blank and rel containing noopener; that .synthesis-body holds h3 and li; and that the panel's innerText contains no '##', no '**' and no line starting with '- '. Test three opens 'titled' and asserts .detail-summary holds no h1, its first heading is the h2 'Overview', and the title text appears in the panel only inside .detail-title. Test two opens 'hostile' and asserts the safe heading rendered as h2 and the safe link as a; that #detailContent contains no script element, no element with an onerror attribute, no img element and no a whose href starts with javascript:; that the page factory recorded no blocked non-loopback request; and that window.__xss is undefined after the image has had time to error. The existing overflow test's locator changes from '.detail-synthesis h4' to '.synthesis-body h3' with the same accent-color assertion, and the 'long' item's 200-character tokens now pass through marked, so overflow stays proven for rendered markdown. An API integration test asserts GET /assets/marked.umd.js and /assets/purify.min.js return 200 with a JavaScript content type and bodies whose sha256 equals VENDORED.json, that GET /assets/missing.js is 404 and not index.html, and that a non-API path still returns index.html. A unit test asserts each file named in VENDORED.json exists, hashes to its recorded sha256, and carries a banner naming its recorded version ('marked v18.0.14', 'DOMPurify 3.4.16'). Tests one, two and three are shown red against today's index.html; test three is also shown red with the title drop removed from renderMarkdown, and test one stays green with it, proving a non-matching h1 survives; test two is also shown red with DOMPurify.sanitize removed from renderMarkdown, where the onerror handler fires.

## Commitments

- The reading summary and the deep synthesis in the web detail panel are rendered from markdown into HTML elements: headings, bullet and numbered lists, bold, inline code and links, with no literal ##, ** or leading '- ' left in the panel text.
- A reading summary's leading h1 is dropped only when its text equals the article title, so the panel shows the title once; every other h1 renders.
- Rendered markdown passes DOMPurify with an allowlist of only the elements markdown produces: no script, no on* handler attribute, no javascript: link and no img, media or svg element reaches the DOM, a hostile summary runs no code, and it cannot make the page fetch from another host.
- Links inside rendered markdown open in a new tab with rel noopener noreferrer.
- marked 18.0.14 and DOMPurify 3.4.16 are served by the daemon from /assets, byte-identical to their npm releases, with version and sha256 recorded in VENDORED.json and checked by a test; no page request leaves the loopback host.
- Synthesis section headings keep the accent styling, and at 375, 768 and 1024 px the detail panel still never scrolls horizontally with rendered markdown in it.
- renderSimpleMarkdown no longer exists, and an unknown /assets path returns 404 rather than index.html.

## Sacrifices

- DOMPurify 3.4.16, not 3.4.17: the two-week rule skips a release whose fixes cover IN_PLACE, SAFE_FOR_TEMPLATES, shadow roots and XHTML mode, none of which a default string sanitize uses.
- The vendored files have no automated update path: dependabot does not see them, and a bump is a manual copy plus a VENDORED.json edit that the hash test then holds.
- The .map source maps are not shipped, so a browser with devtools open logs a 404 for each.
- No syntax highlighting in code blocks.
- The feed cards' short summaries stay plain text.
- The TUI keeps its own renderSimpleMarkdown in helpers.go; no TUI parity work.
- Chromium only, as in web-first-impression.
- Markdown images render as nothing, since img is outside the allowlist.
- A leading h1 that paraphrases the title rather than copying it verbatim still renders; only an exact trimmed match is dropped.

## Risk

If a vendored script is missing from the wheel or fails to load, every detail panel shows 'Failed to load: marked is not defined' instead of the article; the /assets API test and every browser test that opens a panel fail first. If marked's output for some model text contains markup DOMPurify's defaults allow but the panel's CSS does not contain (a wide table, a long pre line), the panel could overflow horizontally; the .md rules give pre and table their own scrollers and the overflow test covers long tokens. A future DOMPurify bypass in 3.4.16 would be an XSS on a page that holds the API key in localStorage; the summaries are model output over third-party articles, so a hostile article can steer what reaches the renderer, and the tag allowlist narrows what a bypass can use; the bump path is the VENDORED.json edit.

## Licensed by

README.md:77
