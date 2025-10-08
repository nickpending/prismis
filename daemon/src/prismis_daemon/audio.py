"""Audio generation for Prismis reports - Jarvis briefing system.

Uses lspeak for all TTS (system and ElevenLabs providers).
"""

import subprocess
from pathlib import Path
from typing import Optional
import shutil
import logging
import time

from litellm import completion

from .reports import DailyReport
from .config import Config

logger = logging.getLogger(__name__)


# No TTSEngine protocol needed - just use LspeakTTSEngine directly


class AudioScriptGenerator:
    """Generate conversational Jarvis briefing scripts from daily reports."""

    def __init__(self, config: Config):
        """Initialize script generator.

        Args:
            config: Configuration instance for LLM access
        """
        self.config = config

    def generate_script(self, report: DailyReport) -> str:
        """Transform DailyReport into conversational Jarvis briefing script.

        Uses LLM to create personalized commentary with fictional expert consultations.
        Focus on HIGH priority items only for concise 2-5 minute briefings.

        Args:
            report: DailyReport to transform

        Returns:
            Narration script ready for TTS (300-750 words target)

        Raises:
            ValueError: If report has no high priority content
        """
        if not report.high_priority:
            raise ValueError("No high priority content available for briefing")

        # Build context for LLM from high priority items
        content_summaries = []
        for idx, item in enumerate(report.high_priority, 1):
            content_summaries.append(
                f"{idx}. {item.title}\n"
                f"   Source: {item.source_name} ({item.time_ago()})\n"
                f"   Summary: {item.summary}\n"
            )

        items_text = "\n".join(content_summaries)
        date_str = report.generated_at.strftime("%B %d, %Y")

        # LLM prompt for Jarvis briefing generation
        prompt = f"""You are Jarvis, a sophisticated AI advisor providing a personalized tech intelligence briefing.

Generate a conversational 2-5 minute audio briefing (300-750 words) from these HIGH priority items:

{items_text}

**Instructions:**
- Open with: "Good morning. I've been analyzing tech developments overnight..."
- For each item, provide:
  - Concise explanation of what it means
  - Why it's relevant to the user's work/interests
  - Optional: Brief fictional expert consultation ("I discussed this with Sarah from the AI safety community...")
- Connect related items: "This connects to yesterday's PostgreSQL discussion..."
- Close with: "That's your briefing for {date_str}. I'll continue monitoring."

**Style:**
- Conversational, not robotic
- Personal and advisory tone
- Natural pacing (use "..." for pauses between sections)
- Skip URLs, markdown formatting, special characters
- Sound like you're speaking to a colleague, not reading a report

**Length:** 300-750 words (strict - this is for 2-5 minute audio)

Generate the briefing script:"""

        try:
            # Use existing LLM infrastructure
            response = completion(
                model=self.config.llm_model,
                messages=[{"role": "user", "content": prompt}],
                api_key=self.config.llm_api_key,
                api_base=self.config.llm_api_base,
                temperature=0.7,  # Higher creativity for personality
            )

            script = response.choices[0].message.content.strip()

            # Clean up any remaining markdown artifacts
            script = script.replace("**", "")
            script = script.replace("*", "")
            script = script.replace("#", "")

            logger.info(f"Generated Jarvis script: {len(script.split())} words")
            return script

        except Exception as e:
            logger.error(f"Failed to generate Jarvis script: {e}")
            raise RuntimeError(f"LLM script generation failed: {e}") from e


class LspeakTTSEngine:
    """Text-to-speech using lspeak (ElevenLabs/system TTS)."""

    def __init__(
        self,
        provider: str = "elevenlabs",
        voice: Optional[str] = None,
    ):
        """Initialize lspeak TTS engine.

        Args:
            provider: TTS provider (elevenlabs, system)
            voice: Voice ID for provider (optional, uses provider default)
        """
        self.provider = provider
        self.voice = voice

        # Check if lspeak is installed
        if not shutil.which("lspeak"):
            raise RuntimeError(
                "lspeak not found. Install with: "
                "uv tool install git+https://github.com/nickpending/lspeak.git"
            )

        logger.info(f"Initialized lspeak engine (provider: {provider})")

    def generate(self, script: str, output_path: Path) -> None:
        """Generate audio using lspeak subprocess.

        Args:
            script: Text to convert to speech
            output_path: Path for output file

        Raises:
            RuntimeError: If audio generation fails
        """
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build lspeak command
        cmd = [
            "lspeak",
            "--provider",
            self.provider,
            "--no-cache",  # No semantic caching for unique briefings
            "-o",
            str(output_path),
        ]

        if self.voice:
            cmd.extend(["--voice", self.voice])

        cmd.append(script)

        logger.info(f"Generating audio with lspeak (provider: {self.provider})")

        try:
            result = subprocess.run(
                cmd,
                check=True,
                timeout=180,  # 3 minute timeout for API calls
                capture_output=True,
                text=True,
            )

            # Wait for file to be created (TTS takes time)
            max_wait = 30  # seconds
            waited = 0
            while not output_path.exists() and waited < max_wait:
                time.sleep(0.5)
                waited += 0.5

            if not output_path.exists():
                raise RuntimeError(
                    f"Audio file not created after {max_wait}s: {output_path}"
                )

            logger.info(f"Audio generated: {output_path}")
            if result.stdout:
                logger.debug(f"lspeak output: {result.stdout}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Audio generation timed out (>3 minutes)")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or e.stdout or str(e)

            # Provide helpful error messages for common issues
            if "ELEVENLABS_API_KEY" in error_msg:
                raise RuntimeError(
                    "ElevenLabs API key not set. "
                    "Set ELEVENLABS_API_KEY environment variable or use --provider=system"
                ) from e
            elif self.provider == "elevenlabs" and "API" in error_msg:
                # Fallback suggestion for ElevenLabs issues
                logger.warning(f"ElevenLabs API error: {error_msg}")
                raise RuntimeError(
                    f"ElevenLabs API failed: {error_msg}\n"
                    "Try using --provider=system for local TTS fallback"
                ) from e
            else:
                raise RuntimeError(f"lspeak command failed: {error_msg}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error during audio generation: {e}") from e


def get_tts_engine(config: Config) -> LspeakTTSEngine:
    """Factory function to get configured lspeak TTS engine.

    Args:
        config: Configuration with audio settings

    Returns:
        LspeakTTSEngine instance based on config

    Raises:
        ValueError: If audio provider is invalid
        RuntimeError: If lspeak is not installed
    """
    provider = getattr(config, "audio_provider", "system")
    voice = getattr(config, "audio_voice", None)

    # Map legacy "macos" to "system" for backwards compatibility
    if provider == "macos":
        provider = "system"

    if provider not in ("elevenlabs", "system"):
        raise ValueError(
            f"Invalid audio_provider: {provider}. Valid options: elevenlabs, system"
        )

    return LspeakTTSEngine(provider=provider, voice=voice)


def generate_briefing(
    report: DailyReport,
    config: Config,
    output_dir: Optional[Path] = None,
) -> Path:
    """High-level function to generate complete audio briefing.

    Args:
        report: DailyReport to convert to audio
        config: Configuration for TTS and LLM
        output_dir: Output directory (default: ~/Downloads)

    Returns:
        Path to generated audio file

    Raises:
        ValueError: If report has no content
        RuntimeError: If generation fails
    """
    # Default output directory
    if output_dir is None:
        output_dir = Path.home() / "Downloads"

    # Generate filename with date
    date_str = report.generated_at.strftime("%Y-%m-%d")
    output_path = output_dir / f"briefing-{date_str}.mp3"

    # Generate Jarvis script
    logger.info("Generating Jarvis briefing script...")
    script_gen = AudioScriptGenerator(config)
    script = script_gen.generate_script(report)

    # Generate audio
    logger.info("Converting script to audio...")
    engine = get_tts_engine(config)
    engine.generate(script, output_path)

    logger.info(f"Briefing complete: {output_path}")
    return output_path
