"""Desktop notification system for HIGH priority content."""

import logging
import subprocess
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class Notifier:
    """Sends desktop notifications for HIGH priority content."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the notifier.

        Args:
            config: Optional configuration dict with:
                - high_priority_only: Only notify for HIGH priority (default: True)
                - command: Notification command (default: terminal-notifier)
        """
        self.config = config or {}
        self.high_priority_only = self.config.get("high_priority_only", True)
        self.command = self.config.get("command", "terminal-notifier")

    def notify_new_content(self, items: List[Dict[str, Any]]) -> None:
        """Send desktop notification for new content items.

        Args:
            items: List of content items with 'priority', 'title', etc.
        """
        if not items:
            return

        # Filter for HIGH priority items only
        high_items = [item for item in items if item.get("priority") == "high"]

        if not high_items:
            logger.debug("No HIGH priority items to notify about")
            return

        try:
            self._send_notification(high_items)
        except Exception as e:
            logger.warning(f"Failed to send notification: {e}")

    def _send_notification(self, high_items: List[Dict[str, Any]]) -> None:
        """Send the actual notification using terminal-notifier.

        Args:
            high_items: List of HIGH priority items
        """
        count = len(high_items)

        if count == 1:
            title = high_items[0].get("title", "New Content")[:50]
            message = "1 new high priority item"
        else:
            title = "Prismis"
            message = f"{count} new high priority items"

        # Build terminal-notifier command
        cmd = [
            self.command,
            "-title",
            "Prismis",
            "-subtitle",
            title,
            "-message",
            message,
            "-sound",
            "default",
        ]

        # Execute notification command
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            logger.warning(f"Notification command failed: {result.stderr}")
        else:
            logger.info(f"Sent notification: {message}")
