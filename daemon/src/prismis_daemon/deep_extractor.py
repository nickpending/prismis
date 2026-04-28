"""Deep extraction: second-tier LLM synthesis using gpt-5-mini."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llm_core import complete

from .circuit_breaker import get_circuit_breaker
from .observability import log as obs_log

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised when the deep-extract circuit breaker is open.

    Distinct exception type so callers (api.extract_entry) can route to 503
    without inspecting message text. Inherits from RuntimeError for backward
    compatibility with any caller still catching RuntimeError generically.
    """


@dataclass
class DeepExtraction:
    """Result of deep synthesis extraction."""

    synthesis: str
    quotables: list[str]
    model: str
    extracted_at: str  # ISO UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "synthesis": self.synthesis,
            "quotables": self.quotables,
            "model": self.model,
            "extracted_at": self.extracted_at,
        }


class ContentDeepExtractor:
    """Produces deep synthesis (counterintuitive findings, quotables) via a
    second-tier LLM. Separate circuit breaker from the light summarizer."""

    def __init__(self, service_name: str):
        """Initialize the deep extractor with llm-core service.

        Args:
            service_name: Service name from ~/.config/llm-core/services.toml
                          (typically "prismis-openai-deep")
        """
        self.service_name = service_name

        logger.info(
            f"ContentDeepExtractor initialized with service: {self.service_name}"
        )

    def extract(
        self, content: str, title: str = "", url: str = ""
    ) -> dict[str, Any] | None:
        """Generate deep synthesis from content.

        Args:
            content: The content to extract synthesis from
            title: Optional title of the content
            url: Optional URL of the content

        Returns:
            Dict with synthesis, quotables, model, extracted_at, or None if
            content empty or LLM output invalid.

        Raises:
            CircuitOpenError: If circuit breaker is open (subclass of RuntimeError).
            Exception: Any LLM error. Caller is responsible for handling
                       (orchestrator catches to honor INV-002).
        """
        if not content or not content.strip():
            return None

        system_prompt = self._system_prompt()
        prompt = self._user_prompt(content=content, title=title, url=url)

        # INV-001: get_circuit_breaker is keyed to the deep service_name
        # (e.g. "prismis-openai-deep") — independent from the light summarizer's
        # circuit. Failures here never affect the light circuit.
        circuit = get_circuit_breaker(self.service_name)
        if not circuit.check_can_proceed():
            status = circuit.get_status()
            raise CircuitOpenError(
                f"Deep extract circuit open (quota exhausted). "
                f"Recovery in {status.get('recovery_in_seconds', 'unknown')}s"
            )

        try:
            # No temperature kwarg: gpt-5-mini (and other reasoning-class models
            # routed through prismis-openai-deep) reject custom temperature with
            # ProviderError 400. llm_core only attaches `temperature` to the API
            # body when non-None (providers/openai.py:40), so omitting it here
            # keeps the deep service compatible. Do not reintroduce.
            result = complete(
                prompt=prompt,
                system_prompt=system_prompt,
                service=self.service_name,
                json=True,
            )

            tokens = {
                "prompt": result.tokens.input,
                "completion": result.tokens.output,
                "total": result.tokens.input + result.tokens.output,
            }

            obs_log(
                "llm.call",
                action="deep_extract",
                model=result.model,
                tokens=tokens,
                cost_usd=result.cost,
                duration_ms=result.duration_ms,
                status="success",
            )

            circuit.record_success()

        except Exception as e:
            circuit.record_failure(e)

            obs_log(
                "llm.call",
                action="deep_extract",
                model=self.service_name,
                status="error",
                error=str(e),
            )
            raise

        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse deep extraction JSON: {e}")
            return None

        if "synthesis" not in parsed:
            logger.error("Missing 'synthesis' in deep extraction response")
            return None

        extraction = DeepExtraction(
            synthesis=parsed["synthesis"],
            quotables=parsed.get("quotables", []),
            model=result.model,
            extracted_at=datetime.now(UTC).isoformat(),
        )
        return extraction.to_dict()

    def _system_prompt(self) -> str:
        return """You are a sharp, skeptical analyst writing a private note to a smart friend about what's actually interesting in this content. Read with a bullshit detector on. The content has already been light-summarized — your job is what a light summary cannot do.

Surface:
- The counterintuitive or surprising finding
- The buried lede — the specific detail most readers will miss
- The "so what" — what this changes about how someone thinks or acts
- What the source itself reveals about its own reliability — caveats it glosses, self-interest visible in tone, internal inconsistencies, claims it makes without evidence in its own pages
- 1-3 quotable lines, verbatim, only if they're genuinely worth quoting

Stay grounded in what the source actually shows. Don't editorialize beyond the evidence — if the source doesn't claim it, you don't either.

Skepticism is source-internal only. Do not use outside knowledge to challenge claims, do not ask the reader to verify anything, do not go external. If the source is solid, skip Pushback entirely — don't manufacture skepticism for content that doesn't earn it.

Example synthesis (for an article comparing serial bash-loop execution of a 14-task PRD against parallel "Agent Teams" execution — same model, same PRD; 4x faster wall-clock; bash-loop produced 914-line learning journal, Agent Teams produced 37 lines):

**Counterintuitive:** running the exact same model and PRD in a parallel "Agent Teams" setup shaves wall-clock time by 4x but destroys almost everything you'd call project memory.

**Buried lede:** the bash loop produced a 914-line learning journal; Agent Teams produced 37 lines. That's not "less verbosity" — it's lost cross-task learning.

**So what:** speed matters less than people think when the cost is the team's memory of what it just learned. Reach for parallel agents only when cross-task carryover isn't needed.

Quotables: ["Parallel agents win the stopwatch; a serial shell loop wins the memory."]

Return ONLY valid JSON. Start with { and end with }. No preamble.

Schema:
{
  "synthesis": "Markdown text. Use **Counterintuitive:**, **Buried lede:**, **So what:**, and **Pushback:** as labeled sections when each applies (skip a label if the source doesn't support it; **Pushback:** appears only when the source's own contents reveal limits worth flagging). Cite specific data, numbers, and named entities from the source.",
  "quotables": ["Verbatim quote from the source", "Another verbatim quote"]
}

quotables: 0-3 items. Must be EXACT text from the content, not paraphrase. Include only if the line stands on its own as quotable. If nothing meets that bar, return []."""

    def _user_prompt(self, content: str, title: str, url: str) -> str:
        header = f"# Title\n{title}"
        if url:
            header += f"\n\n# Source\n{url}"
        return f"""{header}

# Content
{content}

Produce the synthesis JSON. Lead with what's counterintuitive or buried. Only quote lines that genuinely stand on their own. Stay grounded in the source. Add **Pushback:** only when the source itself reveals weak claims — never use outside knowledge."""
