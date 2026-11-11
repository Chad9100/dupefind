"""Long path support for Windows.

Classic Win32 calls fail on paths of 260 characters or more (folders: about
248). The ``\\\\?\\`` prefix lifts that limit. :func:`io_path` is used for every
file system call; paths shown to the user and stored in plans stay plain.
"""

from __future__ import annotations

import ntpath
import os

# Prefix only when needed: the extended form disables some path normalisation,
# so short paths are passed through untouched.
LONG_THRESHOLD = 240

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"


def to_extended(path: str, *, threshold: int = LONG_THRESHOLD) -> str:
    """Windows-style ``path`` in extended-length form if it is long.

    Pure string logic (uses ``ntpath``), so it behaves the same on any OS.
    """
    if len(path) < threshold or path.startswith(_PREFIX):
        return path
    path = ntpath.abspath(path)
    if path.startswith("\\\\"):  # UNC share: \\server\share\x -> \\?\UNC\server\share\x
        return _UNC_PREFIX + path[2:]
    if ntpath.splitdrive(path)[0]:
        return _PREFIX + path
    return path


def io_path(path: str) -> str:
    """``path`` ready for file system calls; a no-op except on Windows."""
    if os.name != "nt":
        return path
    return to_extended(path)


def display_path(path: str) -> str:
    """Strip the extended-length prefix again (for messages and reports)."""
    if path.startswith(_UNC_PREFIX):
        return "\\\\" + path[len(_UNC_PREFIX):]
    if path.startswith(_PREFIX):
        return path[len(_PREFIX):]
    return path
