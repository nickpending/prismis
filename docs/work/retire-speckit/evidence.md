# retire-speckit — post-merge walks

Run 2026-10-29 after the lane merged job 1 (3b6a261) and closed (375a411) on main.

**C9, the merged tree pushed from the main checkout.** `git push origin main` (375a411) went through ember's pre-push hook and landed on origin; the hook skipped re-running the checks for the tree the lane had already gated.

**C2, ember loads the project's constitution.** `ember lane status --work-order retire-speckit` ran in the main checkout without refusal. Ember's own loader (`loadArticles` in ember's shared/constitution/src/loader.ts, called with this repo's root) returned fifteen article ids: the eleven universal ones followed by `local-first-zero-ops`, `three-components-one-contract`, `pipeline-runs-unattended` and `llm-providers-only-external-boundary`.

**C7, CI on both runners with make test as the test step.** The first run on 375a411 (40908387499) was green on ubuntu-latest and red on macos-latest, twice, on one pre-existing daemon test: `test_daemon_help_command` timed out at 10 s because `prismis-daemon --help` imported sentence-transformers and torch at module load (3.85 s of import on a Mac, measured with `python -X importtime`). That is #107, fixed in 88dbbe3 by importing the embedding stack where it is used, with a test that imports the entry point in a fresh interpreter and asserts neither package loaded. CI on 88dbbe3 (40916128457) completed green on both ubuntu-latest and macos-latest, each with its `make test` step succeeding.

Checked once by reading the push output, the loader's output and the CI runs; the loader result and CI are not standing tests of this repo.
