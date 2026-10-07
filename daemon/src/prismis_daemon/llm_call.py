"""Shared LLM-call mechanics behind a service's circuit breaker.

dedup-triage.md's cluster 11: context_analyzer.py, evaluator.py and summarizer.py each
hand-wrote the same call shape -- the circuit-breaker check, the complete() call,
token/cost extraction, and the obs_log success/failure recording. This module is the
one home for that mechanical part. JSON parsing and error handling (raise vs. return
None) differ across the three callers and stay with each of them, not here.

context_analyzer.py previously made no circuit-breaker check at all, unlike the other
two. Per the work order's SC-8, this helper closes that gap deliberately: all three
callers now refuse a call while their service's circuit is open, not just two of them.
"""

from dataclasses import dataclass

from .circuit_breaker import get_circuit_breaker
from .llm_client import CompleteResult, complete
from .observability import log as obs_log

# The most UTF-8 bytes of article text any prompt carries. Sized for the 400,000-token
# window of the model both production services run: a byte-level BPE token covers at
# least one byte, so 350,000 bytes never exceed 350,000 tokens, leaving the rest of
# the window for the system prompt, the user's context and the reply.
MAX_CONTENT_BYTES = 350_000


@dataclass(frozen=True)
class BoundedContent:
    """Article text as it goes into a prompt, with what the bound did to it."""

    text: str
    sent_bytes: int
    total_bytes: int

    @property
    def record(self) -> dict[str, int] | None:
        """The `content_bounded` analysis value, or None when nothing was cut."""
        if self.sent_bytes >= self.total_bytes:
            return None
        return {"sent_bytes": self.sent_bytes, "total_bytes": self.total_bytes}


def bound_content(content: str) -> BoundedContent:
    """Bound article text to MAX_CONTENT_BYTES UTF-8 bytes for a prompt.

    The one place the bound is decided for the summarizer, evaluator and deep
    extractor. Content within the bound is returned unchanged. Longer content keeps
    its leading bytes, cut on a character boundary, followed by one line telling the
    model the text was cut and how much of it remains, so a part is not summarized
    as the whole.
    """
    encoded = content.encode(errors="replace")
    total = len(encoded)
    if total <= MAX_CONTENT_BYTES:
        return BoundedContent(content, total, total)
    kept = encoded[:MAX_CONTENT_BYTES].decode(errors="ignore")
    sent = len(kept.encode(errors="replace"))
    note = (
        f"\n\n[The article text above was cut: it shows the first {sent:,} of "
        f"{total:,} UTF-8 bytes. Treat it as the opening part of a longer article.]"
    )
    return BoundedContent(kept + note, sent, total)


def build_messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    """Build the two-message system/user chat payload each _build_prompt-style
    caller returns. call_llm_with_circuit_breaker takes system_prompt/user_prompt
    back apart again (its callers extract messages[0]/messages[1] before calling
    it), so this is the one place the list literal itself lives.
    """
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def call_llm_with_circuit_breaker(
    service_name: str, system_prompt: str, user_prompt: str, action: str
) -> CompleteResult:
    """Call the LLM behind service_name's circuit breaker, recording the outcome.

    Args:
        service_name: llm-core service name to resolve and to key the circuit breaker
        system_prompt: system prompt to send
        user_prompt: user prompt to send
        action: the obs_log "action" field (e.g. "evaluate", "summarize",
            "context_analysis") -- callers keep their own existing value so this move
            does not change what an observer sees.

    Returns:
        The CompleteResult from llm_client.complete() on success.

    Raises:
        RuntimeError: if the circuit is open (quota exhausted). No LLM call is made.
        Exception: whatever complete() itself raised, re-raised after being recorded
            as a circuit-breaker failure and logged.
    """
    circuit = get_circuit_breaker(service_name)
    if not circuit.check_can_proceed():
        status = circuit.get_status()
        obs_log(
            "llm.call",
            action=action,
            model=service_name,
            status="circuit_open",
        )
        raise RuntimeError(
            f"LLM circuit breaker is open (quota exhausted). "
            f"Recovery in {status.get('recovery_in_seconds', 'unknown')}s"
        )

    try:
        result = complete(
            prompt=user_prompt,
            system_prompt=system_prompt,
            service=service_name,
            json=True,
        )

        tokens = {
            "prompt": result.tokens.input,
            "completion": result.tokens.output,
            "total": result.tokens.input + result.tokens.output,
        }

        # Real billed cost for OpenRouter services; None for api.openai.com
        # services, which return no cost (see llm_client.py's complete()).
        obs_log(
            "llm.call",
            action=action,
            model=result.model,
            tokens=tokens,
            cost_usd=result.cost,
            duration_ms=result.duration_ms,
            status="success",
        )

        # Record success for circuit breaker (closes if half-open)
        circuit.record_success()

    except Exception as e:
        # Record failure for circuit breaker (may open circuit)
        circuit.record_failure(e)

        obs_log(
            "llm.call",
            action=action,
            model=service_name,
            status="error",
            error=str(e),
        )
        raise  # Re-raise to preserve existing error handling

    return result
