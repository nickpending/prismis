# menubar-app — post-merge walk

**C2, run 2026-10-27 after deploying 2e8ea9b to the operator's remote host** (daemon restarted 21:58:18 UTC; the served page carries `ITEM_LINK`).

- Chromium, with the operator's `[remote]` key in localStorage, opened `<remote url>/#item=5d937428-3c20-4b87-b342-c7f7a7f615cd` (a current unread HIGH item). The detail panel opened and showed that item's title, "GhostAction Crosses Paths With PolinRider".
- A fresh load of `<remote url>/#item=../prune/count` left the panel hidden and sent no request to `/api/prune` or any `/api/entries/<id>` path.

Run as a one-off Playwright script against the live daemon; no standing test asserts the live deployment.
