"""Daemon single-instance lock."""

import fcntl
import os
import sys
from pathlib import Path


class DaemonLock:
    """Prevents multiple daemon instances via fcntl file lock."""

    def __init__(self):
        state_home = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
        self.pid_file = Path(state_home) / "prismis" / "daemon.pid"
        self._handle = None

    def __enter__(self):
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.pid_file, "w")
        try:
            fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Daemon already running")
        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return self

    def __exit__(self, *_):
        pass  # fcntl releases lock when process exits


def acquire_daemon_lock():
    """Convenience function for use with 'with' statement."""
    return DaemonLock()
