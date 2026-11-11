"""Find duplicate files.

Candidates are narrowed in three passes so that most files are never read in
full: group by size, then by a SHA-256 of the first 64 KiB, then by a SHA-256
of the whole file. Symlinks (and Windows junctions) are never followed, and
files that cannot be read are reported instead of aborting the scan.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Sequence

from .cache import HashCache
from .longpath import io_path

CHUNK_SIZE = 64 * 1024
READ_BLOCK = 1024 * 1024

# A file with this name marks a dupefind quarantine folder; scans skip it so
# quarantined copies are not reported as duplicates of the files that were kept.
QUARANTINE_MARKER = ".dupefind-quarantine"

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime: float
    root_index: int = 0


@dataclass
class DupGroup:
    size: int
    sha256: str
    files: list[FileInfo]

    @property
    def wasted(self) -> int:
        return self.size * (len(self.files) - 1)


@dataclass(frozen=True)
class ScanError:
    path: str
    message: str


@dataclass
class ScanResult:
    roots: list[str]
    groups: list[DupGroup] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    hardlinks_skipped: int = 0
    bytes_hashed: int = 0
    cache_hits: int = 0

    @property
    def wasted(self) -> int:
        return sum(g.wasted for g in self.groups)

    @property
    def duplicate_files(self) -> int:
        return sum(len(g.files) for g in self.groups)

    @property
    def redundant_copies(self) -> int:
        return sum(len(g.files) - 1 for g in self.groups)


def hash_file(path: str, limit: int | None = None) -> str:
    """SHA-256 hex digest of a file, or of its first ``limit`` bytes."""
    h = hashlib.sha256()
    remaining = limit
    with open(io_path(path), "rb") as fh:
        while True:
            want = READ_BLOCK if remaining is None else min(READ_BLOCK, remaining)
            if want <= 0:
                break
            block = fh.read(want)
            if not block:
                break
            h.update(block)
            if remaining is not None:
                remaining -= len(block)
    return h.hexdigest()


def _error_text(exc: OSError) -> str:
    return exc.strerror or str(exc)


def is_link(st: os.stat_result) -> bool:
    """True for symlinks and for Windows reparse points such as junctions."""
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _device(path: str) -> int | None:
    """Device id of ``path`` (DirEntry.stat leaves it 0 on Windows, so lstat)."""
    try:
        return os.lstat(io_path(path)).st_dev
    except OSError:
        return None


def _matches(rel: str, name: str, patterns: Sequence[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
    return False


def iter_files(
    roots: Sequence[str],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    min_size: int = 1,
    errors: list[ScanError] | None = None,
    one_file_system: bool = False,
) -> Iterator[FileInfo]:
    """Yield regular files under ``roots`` that pass the filters.

    Glob patterns are matched against both the file name and the path relative
    to its root (with ``/`` separators). ``exclude`` also prunes directories.
    With ``one_file_system``, folders on a different device than their root
    (mount points) are not entered.
    """
    if errors is None:
        errors = []
    seen: set[str] = set()

    def accept(path: str, rel: str, name: str, st: os.stat_result, idx: int):
        if st.st_size < min_size:
            return None
        if include and not _matches(rel, name, include):
            return None
        if exclude and _matches(rel, name, exclude):
            return None
        key = os.path.normcase(path)
        if key in seen:
            return None
        seen.add(key)
        return FileInfo(path, st.st_size, st.st_mtime, idx)

    for idx, root in enumerate(roots):
        root_abs = os.path.abspath(root)
        try:
            st = os.lstat(io_path(root_abs))
        except OSError as exc:
            errors.append(ScanError(root_abs, _error_text(exc)))
            continue
        if is_link(st):
            errors.append(ScanError(root_abs, "is a symlink; skipped"))
            continue
        if stat.S_ISREG(st.st_mode):
            name = os.path.basename(root_abs)
            info = accept(root_abs, name, name, st, idx)
            if info:
                yield info
            continue
        if not stat.S_ISDIR(st.st_mode):
            errors.append(ScanError(root_abs, "not a regular file or directory"))
            continue

        root_dev = _device(root_abs) if one_file_system else None
        stack = [(root_abs, "")]
        while stack:
            dirpath, relbase = stack.pop()
            if os.path.exists(io_path(os.path.join(dirpath, QUARANTINE_MARKER))):
                continue
            try:
                with os.scandir(io_path(dirpath)) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as exc:
                errors.append(ScanError(dirpath, _error_text(exc)))
                continue
            subdirs = []
            for entry in entries:
                rel = f"{relbase}/{entry.name}" if relbase else entry.name
                # Not entry.path: that would carry the extended-length prefix.
                epath = os.path.join(dirpath, entry.name)
                try:
                    est = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    errors.append(ScanError(epath, _error_text(exc)))
                    continue
                if is_link(est):
                    continue
                if stat.S_ISDIR(est.st_mode):
                    if exclude and _matches(rel, entry.name, exclude):
                        continue
                    if root_dev is not None and _device(epath) != root_dev:
                        continue
                    subdirs.append((epath, rel))
                elif stat.S_ISREG(est.st_mode):
                    info = accept(epath, rel, entry.name, est, idx)
                    if info:
                        yield info
            stack.extend(reversed(subdirs))


def _drop_hardlinks(files: list[FileInfo], errors: list[ScanError]) -> tuple[list[FileInfo], int]:
    """Collapse paths that point at the same physical file (hard links)."""
    kept: list[FileInfo] = []
    ids: set[tuple[int, int]] = set()
    dropped = 0
    for f in files:
        try:
            st = os.stat(io_path(f.path))
        except OSError as exc:
            errors.append(ScanError(f.path, _error_text(exc)))
            continue
        if st.st_ino:
            ident = (st.st_dev, st.st_ino)
            if ident in ids:
                dropped += 1
                continue
            ids.add(ident)
        kept.append(f)
    return kept, dropped


ProgressCallback = Callable[[int, int], None]
_Mapper = Callable[[Callable, Iterable], Iterator]


def _try_hash(path: str, limit: int | None) -> tuple[str | None, OSError | None]:
    try:
        return hash_file(path, limit), None
    except OSError as exc:
        return None, exc


def _hash_all(
    files: Sequence[FileInfo],
    limit: int | None,
    result: ScanResult,
    mapper: _Mapper,
    progress: ProgressCallback | None = None,
    cache: HashCache | None = None,
) -> dict[tuple[int, str], list[FileInfo]]:
    """Hash ``files`` and bucket them by (size, digest).

    ``mapper`` is ``map`` or a thread pool's ``map``; either way results come
    back in input order and are recorded on this thread, so ``result`` and
    ``progress`` are never touched concurrently.
    """
    buckets: dict[tuple[int, str], list[FileInfo]] = defaultdict(list)
    digests: dict[str, str] = {}
    todo: list[FileInfo] = []
    for f in files:
        cached = cache.get(f.path, f.size, f.mtime, limit) if cache is not None else None
        if cached is None:
            todo.append(f)
        else:
            digests[f.path] = cached
    outcomes = iter(mapper(lambda f: _try_hash(f.path, limit), todo))
    for f in files:
        digest = digests.get(f.path)
        if digest is not None:
            result.cache_hits += 1
        else:
            digest, exc = next(outcomes)
            if exc is not None:
                result.errors.append(ScanError(f.path, _error_text(exc)))
                continue
            result.bytes_hashed += f.size if limit is None else min(f.size, limit)
            if cache is not None:
                cache.put(f.path, f.size, f.mtime, limit, digest)
        if progress is not None:
            progress(result.files_scanned, result.bytes_hashed)
        buckets[(f.size, digest)].append(f)
    return buckets


def find_duplicates(
    roots: Sequence[str],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    min_size: int = 1,
    progress: ProgressCallback | None = None,
    jobs: int = 1,
    cache: HashCache | None = None,
    one_file_system: bool = False,
) -> ScanResult:
    """Scan ``roots`` and return groups of byte-identical files.

    ``progress``, if given, is called as ``progress(files_seen, bytes_hashed)``
    after each file is found and after each file is hashed.

    ``jobs`` is the number of threads that hash files at the same time. The
    result is identical for any value; only the speed changes.

    ``cache``, if given, supplies digests for files whose size and mtime are
    unchanged and records new ones; the caller saves it.
    """
    if jobs < 1:
        raise ValueError("jobs must be at least 1")
    result = ScanResult(roots=[os.path.abspath(r) for r in roots])
    errors = result.errors

    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for info in iter_files(
        roots, include=include, exclude=exclude, min_size=min_size, errors=errors,
        one_file_system=one_file_system,
    ):
        result.files_scanned += 1
        result.bytes_scanned += info.size
        by_size[info.size].append(info)
        if progress is not None:
            progress(result.files_scanned, result.bytes_hashed)

    candidates: list[FileInfo] = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        same_size, dropped = _drop_hardlinks(same_size, errors)
        result.hardlinks_skipped += dropped
        if len(same_size) >= 2:
            candidates.extend(same_size)

    # All candidates go through each pass together, so that a thread pool has
    # enough work even when most groups hold only two files.
    pool = ThreadPoolExecutor(max_workers=jobs) if jobs > 1 and candidates else None
    mapper: _Mapper = pool.map if pool is not None else map
    try:
        matches: list[tuple[int, str, list[FileInfo]]] = []
        need_full: list[FileInfo] = []
        heads = _hash_all(candidates, CHUNK_SIZE, result, mapper, progress, cache)
        for (size, head_digest), same_head in heads.items():
            if len(same_head) < 2:
                continue
            if size <= CHUNK_SIZE:
                # The first 64 KiB is the whole file, so this is the full hash.
                matches.append((size, head_digest, same_head))
            else:
                need_full.extend(same_head)
        fulls = _hash_all(need_full, None, result, mapper, progress, cache)
        matches.extend((size, digest, members) for (size, digest), members in fulls.items())
    except BaseException:
        if pool is not None:
            # Ctrl-C (or a bug): drop queued work instead of hashing it all first.
            pool.shutdown(wait=False, cancel_futures=True)
            pool = None
        raise
    finally:
        if pool is not None:
            pool.shutdown()

    groups: list[DupGroup] = []
    for size, digest, members in matches:
        if len(members) < 2:
            continue
        members.sort(key=lambda f: (f.root_index, f.path))
        groups.append(DupGroup(size, digest, members))

    groups.sort(key=lambda g: (-g.wasted, g.files[0].path))
    result.groups = groups
    return result
