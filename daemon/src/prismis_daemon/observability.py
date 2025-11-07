"""Observability logging for Prismis daemon - JSONL event tracking."""

import fcntl
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class ObservabilityLogger:
    """Thread-safe and process-safe JSONL event logger with daily rotation."""

    def __init__(self, base_dir: Path | None = None):
        """Initialize observability logger.

        Args:
            base_dir: Directory for JSONL files. Defaults to ~/.local/share/prismis/observability
        """
        if base_dir is None:
            data_home = Path.home() / ".local" / "share"
            base_dir = data_home / "prismis" / "observability"

        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **metadata: Any) -> None:
        """Log an event with metadata to daily JSONL file.

        Thread-safe and process-safe via fcntl file locking.
        Gracefully degrades on failure - prints to stderr but doesn't crash.

        Args:
            event: Event name (e.g., "daemon.cycle.start", "llm.call")
            **metadata: Additional event metadata
        """
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.base_dir / f"{today}_events.jsonl"

        entry = {"ts": datetime.utcnow().isoformat() + "Z", "event": event, **metadata}

        # Retry wrapper for lock failures (3 attempts with exponential backoff)
        for attempt in range(3):
            try:
                with open(log_file, "a") as f:
                    # Acquire exclusive lock (blocks other processes/threads)
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        f.write(json.dumps(entry) + "\n")
                        f.flush()  # Force to disk immediately
                    finally:
                        # Always release lock
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return  # Success
            except BlockingIOError:
                # Lock timeout - retry with backoff
                if attempt < 2:
                    time.sleep(0.01 * (attempt + 1))  # 10ms, 20ms
                else:
                    print(
                        f"[Observability] Failed to log event after 3 attempts: {event}",
                        file=sys.stderr,
                    )
            except Exception as e:
                # Any other error - log to stderr and give up
                print(
                    f"[Observability] Error logging event '{event}': {e}",
                    file=sys.stderr,
                )
                return

    def cleanup_old_files(self, retention_days: int = 30) -> int:
        """Remove JSONL files older than retention_days.

        Args:
            retention_days: Number of days to keep. Default: 30

        Returns:
            Number of files removed
        """
        if not self.base_dir.exists():
            return 0

        cutoff_date = datetime.now() - timedelta(days=retention_days)
        removed_count = 0

        for file_path in self.base_dir.glob("*_events.jsonl"):
            try:
                # Extract date from filename: YYYY-MM-DD_events.jsonl
                date_str = file_path.stem.split("_")[0]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")

                if file_date < cutoff_date:
                    file_path.unlink()
                    removed_count += 1
            except (ValueError, IndexError):
                # Invalid filename format - skip
                continue
            except Exception as e:
                print(
                    f"[Observability] Error removing old file {file_path}: {e}",
                    file=sys.stderr,
                )
                continue

        return removed_count


# Global singleton instance for easy access
_logger: ObservabilityLogger | None = None


def get_logger() -> ObservabilityLogger:
    """Get global observability logger instance (singleton pattern)."""
    global _logger
    if _logger is None:
        _logger = ObservabilityLogger()
    return _logger


def log(event: str, **metadata: Any) -> None:
    """Convenience function to log events using global logger.

    Usage:
        from prismis_daemon.observability import log
        log("daemon.cycle.start", sources=30)
        log("llm.call", action="summarize", tokens={"prompt": 450, "completion": 85})
    """
    get_logger().log(event, **metadata)
