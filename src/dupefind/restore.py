"""Undo ``dupefind apply``: move quarantined files back to where they came from.

The quarantine folder's ``manifest.jsonl`` says where each file came from.
Every record is checked before anything moves: the quarantined file must lie
inside the quarantine folder, must still be a regular file (not a symlink)
with the recorded size and SHA-256, and nothing may exist at the original
path now. A file that is already back, or was replaced in the meantime, is
never overwritten; it is skipped and reported.

After a real restore the manifest is rewritten to list only the files that are
still in quarantine, and folders the restore emptied are removed.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field

from .apply import MANIFEST_NAME, SKIPPED, Action, _check_hash, _check_regular, _norm

RESTORED = "restored"
WOULD_RESTORE = "would restore"


class RestoreError(Exception):
    """The folder is not a dupefind quarantine (or its manifest is unreadable)."""


@dataclass
class RestoreReport:
    quarantine_dir: str
    dry_run: bool
    actions: list[Action] = field(default_factory=list)

    @property
    def done(self) -> list[Action]:
        return [a for a in self.actions if a.status != SKIPPED]

    @property
    def skipped(self) -> list[Action]:
        return [a for a in self.actions if a.status == SKIPPED]

    @property
    def restored_bytes(self) -> int:
        return sum(a.size for a in self.done)


def _inside(path: str, folder: str) -> bool:
    return _norm(path).startswith(_norm(folder) + os.sep)


def _read_manifest(manifest: str) -> list[tuple[str, dict | None]]:
    """Return ``(raw line, record or None)`` for every non-blank line."""
    try:
        with open(manifest, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        raise RestoreError(f"no {MANIFEST_NAME} in {os.path.dirname(manifest)}") from None
    except OSError as exc:
        raise RestoreError(f"cannot read {manifest}: {exc.strerror or exc}") from None
    entries: list[tuple[str, dict | None]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            record = None
        entries.append((line, record))
    return entries


def _valid(record: dict | None) -> str:
    if record is None:
        return "manifest line is not a JSON object"
    for key, kind in (("original", str), ("quarantined", str), ("size", int), ("sha256", str)):
        value = record.get(key)
        if not isinstance(value, kind) or isinstance(value, bool):
            return f"manifest record has no valid {key!r}"
    return ""


def _rewrite_manifest(manifest: str, lines: list[str]) -> None:
    tmp = manifest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(line + "\n" for line in lines)
    os.replace(tmp, manifest)


def _prune_empty_dirs(qdir: str, start: str) -> None:
    folder = os.path.dirname(start)
    while _inside(folder, qdir):
        try:
            os.rmdir(folder)  # only succeeds when empty
        except OSError:
            return
        folder = os.path.dirname(folder)


def restore_quarantine(quarantine_dir: str, *, dry_run: bool = False) -> RestoreReport:
    """Move every file recorded in the quarantine's manifest back to its original path."""
    qdir = os.path.abspath(quarantine_dir)
    if not os.path.isdir(qdir):
        raise RestoreError(f"not a folder: {qdir}")
    manifest = os.path.join(qdir, MANIFEST_NAME)
    entries = _read_manifest(manifest)
    report = RestoreReport(qdir, dry_run)
    keep_lines: list[str] = []
    claimed: set[str] = set()

    for line, record in entries:
        why = _valid(record)
        if why:
            report.actions.append(Action(line[:80], SKIPPED, 0, why))
            keep_lines.append(line)
            continue
        assert record is not None
        original, src = record["original"], record["quarantined"]
        size, digest = record["size"], record["sha256"]

        def skip(reason: str) -> None:
            report.actions.append(Action(original, SKIPPED, 0, reason, dest=src))
            keep_lines.append(line)

        if not os.path.isabs(original) or not os.path.isabs(src):
            skip("manifest paths are not absolute")
            continue
        if not _inside(src, qdir):
            skip(f"quarantined path {src} is outside the quarantine folder")
            continue
        if _inside(original, qdir):
            skip("original path is inside the quarantine folder")
            continue
        if not os.path.lexists(src):
            skip(f"{src} is no longer in the quarantine")
            continue
        st, why = _check_regular(src, size)
        if st is not None:
            why = _check_hash(src, digest)
        if why:
            skip(f"quarantined copy {why}")
            continue
        key = _norm(original)
        if os.path.lexists(original) or key in claimed:
            skip("a file now exists at the original path; not overwriting")
            continue
        claimed.add(key)

        if dry_run:
            report.actions.append(Action(original, WOULD_RESTORE, size, dest=src))
            continue
        try:
            os.makedirs(os.path.dirname(original), exist_ok=True)
            if os.path.lexists(original):  # appeared since the check above
                raise FileExistsError(0, "a file now exists at the original path")
            shutil.move(src, original)
        except OSError as exc:
            skip(f"restore failed: {exc.strerror or exc}")
            continue
        report.actions.append(Action(original, RESTORED, size, dest=src))
        _prune_empty_dirs(qdir, src)

    if not dry_run and report.done:
        _rewrite_manifest(manifest, keep_lines)
    return report
