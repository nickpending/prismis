---
type: exploration
domain: technical
status: draft
started: 2025-10-08
updated: 2026-02-18
tags: [exploration]
---
# Jarvis Audio Briefings - Design Exploration

**Date:** 2025-10-07
**Context:** Task 5.1 Design audio auto-play behavior and integration points
**Status:** Design Complete - Ready for Implementation

## The Evolution

### Initial Approach: Simple TTS
Started with basic text-to-speech of daily reports:
- Read markdown content aloud
- Basic auto-play behavior questions
- Simple file generation with macOS `say` or OpenAI TTS

**The breakthrough:** "This is about audio, not just reading text aloud."

### The Pivot: Podcast-Style Production
Realized we wanted something much more sophisticated:
- Themed, fast-paced podcast format
- Daily tech briefing show style
- Generated from HIGH priority items only
- 2-5 minute target length

**The insight:** "We want to create an actual podcast-style production from daily intelligence."

### The Solution: Jarvis Briefing System
Landed on personalized AI briefing concept:
- **Jarvis as personal tech advisor and analyst**
- **Personal commentary:** "This Rust feature is relevant to your Prismis work"
- **Fictional expert consultations:** "I ran this by Sarah in the AI safety community..."
- **Cross-article synthesis:** "This connects to yesterday's discussion about..."
- **Conversational tone:** Natural briefing, not robotic reading

## Architecture Decisions

### lspeak Integration Discovery
Found that lspeak (existing project) is **perfect** for this:
- ✅ ElevenLabs integration for high-quality voices
- ✅ File output without auto-play: `lspeak -o briefing.mp3 "text"`
- ✅ No semantic caching needed (each briefing is unique)
- ✅ Provider abstraction (ElevenLabs/system TTS)
- ✅ Daemon mode for model pre-loading (though not critical here)
- ✅ Built for Unix pipes and subprocess integration

**Key insight:** "Semantic caching is pointless with large, unique content."

**lspeak is designed for short AI conversations** - we're using it purely as a TTS generation tool, not for its semantic caching or queue management features.

### Clean Integration Pattern
```bash
# Production (ElevenLabs)
lspeak --provider=elevenlabs --no-cache -o briefing-2025-10-07.mp3 "$jarvis_script"

# Development/Testing (System TTS)
lspeak --provider=system --no-cache -o briefing-2025-10-07.wav "$jarvis_script"
```

### API Architecture
**Generation-Only Approach:**
- TUI/API generates files only
- No auto-play from system
- User manually opens/plays files

**Blocking vs Async API Decision:**
- Considered: Job-based async APIs with polling
- **Chosen:** Blocking synchronous generation (10-30 seconds)
- **Rationale:** Simpler UX, no polling complexity, matches command expectations

**RESTful Design (Blocking):**
```
POST /api/audio/briefings → Blocks until complete, returns file info
GET /audio/briefing-2025-10-07.mp3 → Static file serving
```

**File serving via FastAPI StaticFiles (following existing pattern):**
```python
# Add before existing static mount (same pattern as webapp)
audio_dir = Path(config.audio_output_dir)
if audio_dir.exists():
    app.mount("/audio", StaticFiles(directory=str(audio_dir)), name="audio")
```

### Command Design
**Selected:** `:audio summary`
- Descriptive and extensible (future: `:audio article 123`)
- Clear about what it does
- Follows established TUI command patterns

**User Experience:**
```
:audio summary
🎙️ Generating Jarvis briefing...
[Progress spinner for 10-30 seconds]
✅ Briefing ready: briefing-2025-10-07.mp3
```

## Pipeline Flow

1. **Content → Script Generation**
   - **Source:** HIGH priority items ONLY from daily report
   - **LLM generates conversational Jarvis script** with:
     - Opening: "Good morning. I've been analyzing overnight tech developments..."
     - Personal commentary: "This Rust feature is relevant to your Prismis work..."
     - **Fictional expert consultations:**
       - "I ran this by Sarah in the AI safety community..."
       - "My colleague Mike from the startup world thinks..."
     - **Cross-article synthesis:** "This connects to yesterday's PostgreSQL discussion..."
   - **Target length:** 2-5 minutes (roughly 300-750 words of script)

2. **Script → Audio Generation**
   - lspeak subprocess call with full parameter control
   - ElevenLabs provider (fallback to system TTS)
   - **No semantic caching** (`--no-cache` flag)
   - **File naming:** `briefing-YYYY-MM-DD.mp3` (date-based)
   - Output to configured directory (default: `~/Downloads`)

3. **File → HTTP Serving**
   - FastAPI StaticFiles mount at `/audio/`
   - **URL pattern:** `http://localhost:8989/audio/briefing-2025-10-07.mp3`
   - Direct file access, no streaming complexity

## Configuration Design

```toml
[audio]
output_dir = "~/Downloads"           # Where files go
provider = "elevenlabs"              # or "system" fallback
voice = "Rachel"                     # ElevenLabs voice
cleanup_after_days = 7               # Auto-delete old briefings
```

## Error Handling Strategy

**Dependency Issues:**
- **lspeak not installed** → Clear error: "lspeak required. Install: uv tool install git+https://github.com/nickpending/lspeak.git"
- **ElevenLabs API down** → Auto-fallback to system TTS with warning message
- **No ELEVENLABS_API_KEY** → Auto-fallback to system TTS

**Generation Failures:**
- **Empty HIGH priority content** → "No high priority items for briefing"
- **LLM script generation failure** → Clear error message, suggest retry
- **lspeak subprocess failure** → Include lspeak error output in message
- **Audio file creation failure** → Check permissions, disk space

**System Context:**
- **Headless systems** → Generate file only (daemon-only), no auto-play attempts
- **GUI available but no audio system** → Generate file with "Audio system unavailable" message
- **Permission errors** → Clear message about output directory permissions

## Key Design Principles

1. **Generation-only system** - No auto-play complexity
2. **Leverage existing tools** - lspeak for TTS, not reinventing
3. **Blocking command UX** - Simple, familiar pattern
4. **Clean API separation** - RESTful for future integrations
5. **Provider abstraction** - Easy testing with system TTS

## Implementation Ready

**Next Steps:**
1. Jarvis script generation (LLM pipeline)
2. lspeak integration (subprocess calls)
3. Audio API endpoints
4. TUI `:audio summary` command
5. Static file serving setup

**Success Criteria:**
- `:audio summary` generates conversational briefing
- 2-5 minute high-quality audio file
- Jarvis personality with fictional expert consultations
- Clean HTTP serving for file access
- Graceful error handling and provider fallback

## Rejected Alternatives

**Why not job-based async APIs?**
- Would require polling in TUI footer
- Complex for 10-30 second operations
- Blocking with spinner is simpler and more familiar

**Why not auto-play after generation?**
- User explicitly said "GENERATES only, does not play"
- Avoids interrupting user workflow
- Simpler error handling (no audio system detection)

**Why not build TTS from scratch?**
- lspeak already exists and handles ElevenLabs integration
- Don't reinvent working solutions
- Focus on Jarvis script generation (the novel part)

**Why not command names like `:intel` or `:jarvis`?**
- `:intel` feels "dumb" according to user
- `:audio summary` is more descriptive and extensible
- Clear about what the command does

**Why not scheduled auto-generation?**
- Phase 1 focuses on on-demand generation
- Scheduled generation can be added later
- Manual trigger ensures user wants the briefing

## The Core Innovation

**Not just TTS of reports** - this is a **personal AI briefing system** where Jarvis acts as your tech advisor, providing context, opinions, and synthesized insights from your daily intelligence stream.

This transforms Prismis from a content reader into a **personal intelligence briefing service**.