---
type: exploration
domain: technical
status: draft
started: 2026-02-11
updated: 2026-02-18
project: prismis
tags: [exploration, summarization, llm, extraction]
related: [[prismis]]
---

# Deep Extraction: Two-Tier Summarization

**Context:** [[projects/prismis|prismis]] — Current summaries are "book reports" that tell you what something is about, but don't extract the actual insights. Want a "tell me more" layer that's so good you don't need to read the original.

## Problem / Core Question

Prismis surfaces content I care about, but:
1. Still don't have time to read it all
2. Current summaries are high-level — they describe, they don't synthesize
3. Missing the "earthshattering ideas" extraction
4. Discussion threads (Reddit/HN) have no comment synthesis

**Core question:** How do we add a deep extraction layer that captures everything valuable so you literally don't need to consume the original?

## The Evolution

### Initial Approach: Fabric Integration

Started thinking about using Daniel Miessler's Fabric patterns (`extract_wisdom`, etc.) since prismis already has Fabric integration.

**The problem:** Fabric runs locally, outputs to stdout/file. That data is LOST — not associated with the article, not searchable, not stored in prismis. Plus friction of separate invocation.

**The pivot:** Study Fabric's prompt engineering for inspiration, but build natively into prismis so results stay in the system.

### Two-Tier Model Architecture

Realized we need two layers:
- **Light summary**: Triage layer ("what is this, do I care?") — can be even lighter than current
- **Deep extraction**: OMFG layer ("everything valuable, don't need the original")

**Key insight:** These need different models. Light summary = cheap/fast for volume. Deep extraction = quality model for synthesis.

### Model Testing

Ran actual comparisons with the same prompt and content:

| Model | Quality | Tokens | Notes |
|-------|---------|--------|-------|
| gpt-4.1-mini (current) | Lists facts | ~400 | "The author compared two methods..." |
| gpt-4o | Verbose synthesis | ~927 | Better but wordy |
| gpt-5-mini | Sharp synthesis | ~1248 | Nails the insight, quotable |
| gpt-5 | Best punch | ~1785 | "Parallelism without coordination is just faster chaos" |

**The decision:** gpt-5-mini for deep extraction. Quality is there, cost is reasonable. gpt-5 is better but diminishing returns.

### The Deep Extraction Prompt

The magic is in what you ask for:

```
NOT:
- Bullet lists
- "The article discusses X"
- Generic insights

YES:
- The counterintuitive or surprising finding
- Why it matters (the "so what")
- The buried lede — the specific detail everyone should know
- What changes how you think or act
- 1-2 quotable lines

Write like telling a smart friend what was interesting.
```

**Example output (gpt-5-mini):**
> **Counterintuitive:** running the exact same model and PRD in a parallel "Agent Teams" setup shaves wall‑clock time by 4× but destroys almost everything you'd call project memory.
>
> **Buried lede:** the bash loop produced a 914‑line learning journal; Agent Teams produced 37 lines. That's not "less verbosity" — it's lost cross‑task learning.
>
> **Quotable:** "Parallel agents win the stopwatch; a serial shell loop wins the memory."

vs current summary:
> "The author compared two methods for executing a 14-task PRD using Claude Code..."

Night and day.

## Architecture Decisions

### Decision 1: Native over Fabric
- **Rationale:** Data must stay in prismis — searchable, associated with articles, recallable
- **Trade-off:** More work than just calling Fabric, but worth it for system coherence
- **Enables:** Persistent deep extractions, `:extract` command that's run-once

### Decision 2: Two-model config
- **Rationale:** Different jobs need different tools. Volume processing needs cheap/fast. Quality extraction needs a capable model.
- **Implementation:**
```toml
[llm]
model = "gpt-4.1-mini"        # Light summary (volume, triage)
deep_model = "gpt-5-mini"     # Deep extraction (synthesis)
auto_extract = "high"         # "high" | "high+medium" | "all" | "none"
```
- **Trade-off:** More config complexity, but explicit control over cost/quality

### Decision 3: Configurable auto-extract with persistence
- **Rationale:** Auto-extract is expensive, should default to high-priority only. But manual `:extract` should persist so you run-once, get it forever.
- **Implementation:**
  - Auto-extract triggers post-evaluation for items matching `auto_extract` config
  - `:extract` command checks if extraction exists → shows it (no LLM call) or generates and stores
- **Enables:** Cost control + idempotent on-demand extraction

### Decision 4: gpt-5-mini for deep extraction
- **Rationale:** Testing showed gpt-5-mini nails the synthesis at reasonable cost. gpt-5 is ~40% more tokens for marginal quality gain.
- **Trade-off:** Not the absolute best output, but best value
- **Alternative considered:** gpt-5 for on-demand, 5-mini for auto — decided simplicity wins

## Storage Design

Extend existing analysis structure:

```python
analysis = {
    # existing fields stay...
    "reading_summary": "...",
    "alpha_insights": [...],

    # new deep extraction
    "deep_extraction": {
        "synthesis": "The counterintuitive finding...",
        "quotables": ["Line worth sharing..."],
        "model": "gpt-5-mini",
        "extracted_at": "2026-02-11T17:35:00Z"
    }
}
```

## Implementation Plan

1. **Config changes** (`config.py`)
   - Add `llm_deep_model` field
   - Add `auto_extract` field with validation ("high", "high+medium", "all", "none")

2. **Summarizer changes** (`summarizer.py`)
   - Add `deep_extract()` method with synthesis-focused prompt
   - Support model selection per call

3. **Orchestrator changes** (`orchestrator.py`)
   - After evaluation, check if priority matches `auto_extract`
   - If yes, call deep extraction

4. **Storage changes** (`storage.py`, `models.py`)
   - Add `deep_extraction` to analysis schema
   - Migration for existing entries (nullable field)

5. **TUI changes** (`tui/`)
   - Add `:extract` command
   - Check existing extraction, show or generate
   - Display deep extraction in detail view

6. **API changes** (`api.py`)
   - Endpoint for on-demand extraction
   - Return deep_extraction in item response

## Open Questions

- Should `:extract` use a different/better model than auto-extract? (Decided: no, simplicity wins)
- What about re-extraction if prompt improves? (Future: manual override flag?)
- Discussion synthesis (Reddit/HN comments) — separate exploration needed, requires comment ingestion first

## Files Examined

- `daemon/src/prismis_daemon/summarizer.py` — Current summarization architecture
- `daemon/src/prismis_daemon/evaluator.py` — Priority evaluation flow
- `daemon/src/prismis_daemon/config.py` — Config structure
- `~/.config/prismis/config.toml` — Live config example
- Actual prismis data via `prismis-cli get --raw` for testing

## Related Concepts

- [[Fabric]] — Prompt patterns for extraction (inspiration, not integration)
- [[prismis/EXPLORATION-2025-10-28-semantic-search-design]] — Search would benefit from deep extraction content
- Discussion synthesis — Future exploration for Reddit/HN comment ingestion
