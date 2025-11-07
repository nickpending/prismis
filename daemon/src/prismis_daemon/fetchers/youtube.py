"""YouTube content fetcher using yt-dlp."""

import logging
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..models import ContentItem
from ..config import Config
from ..observability import log as obs_log

logger = logging.getLogger(__name__)


class YouTubeFetcher:
    """Fetches video transcripts from YouTube channels.

    Uses yt-dlp subprocess calls to discover videos and extract transcripts.
    Filters videos to only include those from the last N days (configurable).
    """

    def __init__(self, config: Config = None, max_items: int = None):
        """Initialize the YouTube fetcher.

        Args:
            config: Config instance with settings
            max_items: Maximum number of videos to fetch per channel
        """
        if config is None:
            config = Config.from_file()

        self.max_items = max_items or config.get_max_items("youtube")
        self.config = config
        self.yt_dlp_path = shutil.which("yt-dlp")

        if not self.yt_dlp_path:
            raise Exception("yt-dlp not found. Please install it: pip install yt-dlp")

        logger.info(f"YouTube fetcher initialized with yt-dlp at {self.yt_dlp_path}")

    def fetch_content(self, source: Dict[str, Any]) -> List[ContentItem]:
        """Fetch videos with transcripts from a YouTube channel.

        Args:
            source: Source dict with 'url' (YouTube channel URL) and 'id' (source UUID)

        Returns:
            List of ContentItem objects with video transcripts
        """

        start_time = time.time()

        source_url = source.get("url", "")
        source_id = source.get("id", "")

        # Normalize channel URL (support @handles, /c/, /channel/, etc)
        channel_url = self._normalize_channel_url(source_url)

        logger.info(f"Fetching videos from YouTube channel: {channel_url}")

        try:
            # Discover videos from the last N days
            discovery_start = time.time()
            videos = self._discover_channel_videos(channel_url)
            logger.info(f"Discovery took {time.time() - discovery_start:.1f}s")

            if not videos:
                logger.info(f"No recent videos found for channel: {channel_url}")
                return []

            logger.info(
                f"Found {len(videos)} videos from the last {self.config.max_days_lookback} days"
            )

            # Process each video to get transcript
            items = []
            for i, video in enumerate(videos[: self.max_items], 1):
                try:
                    video_start = time.time()
                    logger.info(
                        f"Processing video {i}/{len(videos[: self.max_items])}: {video.get('title', 'Unknown')[:50]}..."
                    )
                    item = self._process_video(video, source_id)
                    if item:
                        items.append(item)
                        logger.info(
                            f"  ✓ Processed in {time.time() - video_start:.1f}s"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to process video {video.get('title', 'Unknown')}: {e}"
                    )
                    continue

            logger.info(
                f"Successfully processed {len(items)} videos with transcripts in {time.time() - start_time:.1f}s total"
            )

            # Log successful fetch
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "fetcher.complete",
                fetcher_type="youtube",
                source_id=source_id,
                source_url=source_url,
                channel_url=channel_url,
                items_count=len(items),
                duration_ms=duration_ms,
                status="success",
            )

            return items

        except Exception as e:
            # Log fetch error
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "fetcher.error",
                fetcher_type="youtube",
                source_id=source_id,
                source_url=source_url,
                channel_url=channel_url,
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            logger.error(f"Failed to fetch YouTube content from {channel_url}: {e}")
            raise Exception(f"YouTube fetch failed for {channel_url}: {e}") from e

    def _normalize_channel_url(self, url: str) -> str:
        """Normalize various YouTube channel URL formats.

        Args:
            url: Channel URL (expecting real URLs only, no youtube:// protocol)

        Returns:
            Normalized YouTube channel URL
        """
        # Strip whitespace
        url = url.strip()

        # Handle @username format (shouldn't happen with normalized URLs, but keep for safety)
        if url.startswith("@"):
            return f"https://www.youtube.com/{url}"

        # Handle bare channel names (shouldn't happen with normalized URLs, but keep for safety)
        if not url.startswith("http"):
            # Assume it's a handle without @
            return f"https://www.youtube.com/@{url}"

        # Already a proper URL, just return it
        return url

    def _discover_channel_videos(self, channel_url: str) -> List[Dict[str, Any]]:
        """Discover recent videos from a YouTube channel.

        Args:
            channel_url: YouTube channel URL

        Returns:
            List of video metadata dicts
        """
        # Build yt-dlp command for discovery WITH FULL METADATA
        # Using --print with specific format (much faster than --print-json)
        # MUST use --simulate with --print together for metadata without download
        cmd = [
            self.yt_dlp_path,
            "--simulate",  # Get metadata without downloading
            "--playlist-end",
            str(self.max_items),  # Limit videos
            "--print",
            "%(id)s|%(title)s|%(duration)s|%(upload_date)s|%(view_count)s|%(webpage_url)s",
            "--socket-timeout",
            "120",  # Increase timeout for reliability
            "--retries",
            "3",  # Add retries like legacy
        ]

        # Add date filter using break-match-filters like legacy
        if self.config.max_days_lookback > 0:
            date_after = (
                datetime.now() - timedelta(days=self.config.max_days_lookback)
            ).strftime("%Y%m%d")
            cmd.extend(["--break-match-filters", f"upload_date>={date_after}"])

        # Add URL as last argument
        cmd.append(channel_url)

        logger.info("Running yt-dlp discovery command...")
        logger.debug(f"Full command: {' '.join(cmd)}")

        try:
            import time

            cmd_start = time.time()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,  # 60 second timeout for discovery
            )
            logger.info(f"yt-dlp command completed in {time.time() - cmd_start:.1f}s")

            if result.returncode != 0:
                # Exit code 101 means break-match-filter stopped at date boundary - this is expected
                if result.returncode == 101 and "--break-match-filters" in str(cmd):
                    logger.debug(
                        "Hit date boundary (exit code 101) - processing videos found"
                    )
                    # Continue processing - we have videos in stdout
                # Check if it's just "no videos found" which is OK
                elif "no videos" in result.stderr.lower() or not result.stdout.strip():
                    return []
                else:
                    logger.error(f"yt-dlp discovery failed: {result.stderr}")
                    raise Exception(f"yt-dlp failed: {result.stderr}")

            videos = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    try:
                        # Parse pipe-delimited format: id|title|duration|upload_date|view_count|url
                        parts = line.split("|")
                        if len(parts) >= 6:
                            video_id, title, duration, upload_date, view_count, url = (
                                parts[:6]
                            )

                            # Date filtering handled by break-match-filters now

                            videos.append(
                                {
                                    "id": video_id,
                                    "title": title,
                                    "url": url
                                    if url.startswith("http")
                                    else f"https://www.youtube.com/watch?v={video_id}",
                                    "duration": int(duration)
                                    if duration and duration != "NA"
                                    else None,
                                    "upload_date": upload_date
                                    if upload_date != "NA"
                                    else None,
                                    "view_count": int(view_count)
                                    if view_count and view_count != "NA"
                                    else None,
                                }
                            )
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse video metadata: {line}, error: {e}"
                        )
                        continue

            return videos

        except subprocess.TimeoutExpired:
            logger.error("Video discovery timed out")
            raise Exception("YouTube channel discovery timed out")

    def _process_video(
        self, video: Dict[str, Any], source_id: str
    ) -> Optional[ContentItem]:
        """Process a video to extract transcript and create ContentItem.

        Args:
            video: Video metadata dict
            source_id: Source UUID

        Returns:
            ContentItem with transcript or None if no transcript available
        """
        video_url = video["url"]
        video_title = video["title"]

        logger.debug(f"Processing video: {video_title}")

        # Extract transcript
        transcript = self._extract_transcript(video_url)

        if not transcript:
            # Handle missing transcript
            return self._handle_missing_transcript(video, source_id)

        # Convert to ContentItem
        return self._to_content_item(video, transcript, source_id)

    def _extract_transcript(self, video_url: str) -> Optional[str]:
        """Extract transcript from a YouTube video using yt-dlp.

        Args:
            video_url: YouTube video URL

        Returns:
            Transcript text or None if not available
        """
        # Use temp directory for subtitle files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Extract video ID from URL for consistent naming
            video_id = (
                video_url.split("watch?v=")[1].split("&")[0]
                if "watch?v=" in video_url
                else video_url.split("/")[-1]
            )

            # Build yt-dlp command for transcript extraction
            cmd = [
                self.yt_dlp_path,
                "--write-auto-sub",  # Get auto-generated subtitles
                "--write-sub",  # Also try manual subtitles
                "--sub-lang",
                "en,en-US,en-GB",  # Try multiple English variants (from legacy)
                "--skip-download",  # Don't download the video
                "--quiet",
                "--no-warnings",
                "--output",
                str(temp_path / "%(id)s.%(ext)s"),
                video_url,
            ]

            logger.debug(f"Extracting transcript for: {video_url}")

            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,  # 60 second timeout per video
                )

                # Check for subtitle files - try multiple patterns (from legacy)
                transcript_text = None

                # Try different subtitle file patterns
                for pattern in [
                    f"{video_id}.en*.vtt",
                    f"{video_id}.en*.srt",
                    f"{video_id}.vtt",
                    f"{video_id}.srt",
                    "*.en.vtt",  # Fallback patterns
                    "*.vtt",
                ]:
                    subtitle_files = list(temp_path.glob(pattern))
                    if subtitle_files:
                        # Use the first matching file
                        transcript_file = subtitle_files[0]
                        logger.debug(f"Found transcript file: {transcript_file.name}")

                        with open(transcript_file, "r", encoding="utf-8") as f:
                            raw_transcript = f.read()

                        # Parse VTT/SRT to plain text
                        transcript_text = self._parse_vtt_transcript(raw_transcript)
                        break

                if not transcript_text:
                    logger.debug(f"No transcript file found for video: {video_url}")
                    return None

                return transcript_text

            except subprocess.TimeoutExpired:
                logger.warning(f"Transcript extraction timed out for: {video_url}")
                return None
            except Exception as e:
                logger.warning(f"Failed to extract transcript: {e}")
                return None

    def _parse_vtt_transcript(self, vtt_content: str) -> str:
        """Parse VTT subtitle file to extract plain text.

        Args:
            vtt_content: Raw VTT file content

        Returns:
            Clean transcript text
        """
        lines = vtt_content.split("\n")
        text_lines = []
        last_line = ""  # Track last line to avoid duplicates

        for line in lines:
            line = line.strip()

            # Skip headers and timestamps
            if (
                line.startswith("WEBVTT")
                or line.startswith("Kind:")
                or line.startswith("Language:")
                or "-->" in line
                or not line
            ):
                continue

            # Skip numbered cue identifiers (just digits)
            if line.isdigit():
                continue

            # Remove HTML tags
            line = re.sub(r"<[^>]+>", "", line)

            # Remove timestamp tags like <00:00:00.000>
            line = re.sub(r"<[\d:.,]+>", "", line)

            # Clean up extra whitespace
            line = " ".join(line.split())

            # Skip duplicate lines (YouTube often repeats lines in captions)
            if line and line != last_line:
                text_lines.append(line)
                last_line = line

        # Join lines with spaces (VTT often splits mid-sentence)
        return " ".join(text_lines)

    def _handle_missing_transcript(
        self, video: Dict[str, Any], source_id: str
    ) -> Optional[ContentItem]:
        """Handle videos that don't have transcripts available.

        Args:
            video: Video metadata
            source_id: Source UUID

        Returns:
            ContentItem with low priority and error note
        """
        # Store video metrics even without transcript
        metrics = {
            "view_count": video.get("view_count"),
            "duration": video.get("duration"),
            "video_id": video.get("id"),
        }

        # Create a ContentItem with minimal content and low priority
        return ContentItem(
            source_id=source_id,
            external_id=video["url"],  # Use URL as external ID
            title=video["title"],
            url=video["url"],
            content=f"Video title: {video['title']}\n\nNo transcript available for this video.",
            published_at=self._parse_upload_date(video.get("upload_date")),
            fetched_at=datetime.utcnow(),
            priority="low",  # Mark as low priority since no transcript
            notes="No transcript available",
            analysis={"metrics": metrics},
        )

    def _to_content_item(
        self, video: Dict[str, Any], transcript: str, source_id: str
    ) -> ContentItem:
        """Convert video data and transcript to ContentItem.

        Args:
            video: Video metadata
            transcript: Extracted transcript text
            source_id: Source UUID

        Returns:
            ContentItem with video content
        """
        # Parse upload date if available
        published_at = self._parse_upload_date(video.get("upload_date"))

        # Store video metrics in analysis field
        metrics = {
            "view_count": video.get("view_count"),
            "duration": video.get("duration"),
            "video_id": video.get("id"),
        }

        return ContentItem(
            source_id=source_id,
            external_id=video["url"],  # Use full URL as external ID for deduplication
            title=video["title"],
            url=video["url"],
            content=transcript,
            published_at=published_at,
            fetched_at=datetime.utcnow(),
            analysis={"metrics": metrics},
        )

    def _parse_upload_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse YouTube's upload date format (YYYYMMDD).

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Parsed datetime or None
        """
        if not date_str:
            return None

        try:
            # Parse date and make it timezone-aware (UTC)
            parsed_date = datetime.strptime(date_str, "%Y%m%d")
            return parsed_date.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug(f"Could not parse date: {date_str}")
            return None
