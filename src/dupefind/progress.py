"""A single, self-overwriting progress line on stderr for long scans."""

from __future__ import annotations

import sys
import time
from typing import Callable, TextIO

from .report import format_size


class ProgressLine:
    """Callable passed to ``find_duplicates(progress=...)``.

    Redraws at most every ``interval`` seconds so that printing never slows
    the scan down, and pads each line so a shorter one fully covers the last.
    Call :meth:`finish` before printing anything else to erase the line.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.interval = interval
        self.clock = clock
        self._last_draw: float | None = None
        self._width = 0

    def __call__(self, files_seen: int, bytes_hashed: int) -> None:
        now = self.clock()
        if self._last_draw is not None and now - self._last_draw < self.interval:
            return
        self._last_draw = now
        text = f"Scanning: {files_seen:,} files seen, {format_size(bytes_hashed)} hashed"
        pad = max(0, self._width - len(text))
        self._width = len(text)
        self.stream.write("\r" + text + " " * pad)
        self.stream.flush()

    def finish(self) -> None:
        """Erase the progress line, if one was drawn."""
        if self._width:
            self.stream.write("\r" + " " * self._width + "\r")
            self.stream.flush()
            self._width = 0
        self._last_draw = None


def stream_is_tty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False
