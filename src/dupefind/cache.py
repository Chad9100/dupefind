"""Persistent hash cache so repeat scans skip files that have not changed.

An entry is keyed by the file's path and remembers the size and modification
time it had when hashed. It is only used while both still match. The cache is
an optimisation for ``scan`` and ``plan``; ``apply`` always re-hashes.
"""

from __future__ import annotations

import json
import os
import tempfile

VERSION = 1


class HashCache:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._entries: dict[str, dict] = {}
        self.dirty = False
        self.warning: str | None = None
        self._load()

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def _kind(limit: int | None) -> str:
        return "full" if limit is None else f"head{limit}"

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.warning = f"ignoring unreadable cache {self.path}: {exc}"
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict) or data.get("version") != VERSION:
            self.warning = f"ignoring cache {self.path}: unknown format"
            return
        self._entries = {k: v for k, v in entries.items() if isinstance(v, dict)}

    def get(self, path: str, size: int, mtime: float, limit: int | None) -> str | None:
        entry = self._entries.get(self._key(path))
        if not entry or entry.get("size") != size or entry.get("mtime") != mtime:
            return None
        digest = entry.get(self._kind(limit))
        return digest if isinstance(digest, str) else None

    def put(self, path: str, size: int, mtime: float, limit: int | None, digest: str) -> None:
        key = self._key(path)
        entry = self._entries.get(key)
        if not entry or entry.get("size") != size or entry.get("mtime") != mtime:
            entry = {"size": size, "mtime": mtime}
            self._entries[key] = entry
        entry[self._kind(limit)] = digest
        self.dirty = True

    def save(self) -> None:
        """Write the cache atomically; does nothing if nothing changed."""
        if not self.dirty:
            return
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".dupefind-cache-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": VERSION, "entries": self._entries}, fh)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.dirty = False
