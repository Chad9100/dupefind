"""Empty old quarantines: permanently delete files quarantined long enough ago.

Only files recorded in the quarantine's ``manifest.jsonl`` are touched, and
only when their record's ``time`` is older than the cutoff. Each one is
checked first, exactly as ``restore`` does: it must lie inside the quarantine
folder and still be a regular file (not a symlink) with the recorded size and
SHA-256. Anything else in the folder is left alone.

After a real purge the manifest is rewritten to list only the files that are
still in quarantine, and folders the purge emptied are removed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .apply import MANIFEST_NAME, SKIPPED, Action, _check_hash, _check_regular
from .restore import RestoreError, _inside, _prune_empty_dirs, _read_manifest, _rewrite_manifest, _valid

PURGED = "purged"
WOULD_PURGE = "would purge"
KEPT = "kept"

_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_AGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)


def parse_age(text: str) -> timedelta:
    """Parse ``30d``, ``12h``, ``2w`` or ``90m`` into a :class:`timedelta`."""
    match = _AGE_RE.match(text)
    if not match:
        raise ValueError(f"invalid age {text!r}: use a number with m, h, d or w, e.g. 30d")
    return timedelta(**{_UNITS[match.group(2).lower()]: float(match.group(1))})


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # Python 3.10's fromisoformat does not accept a trailing "Z".
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


@dataclass
class PurgeReport:
    quarantine_dir: str
    dry_run: bool
    cutoff: datetime
    actions: list[Action] = field(default_factory=list)

    @property
    def done(self) -> list[Action]:
        return [a for a in self.actions if a.status in (PURGED, WOULD_PURGE)]

    @property
    def kept(self) -> list[Action]:
        return [a for a in self.actions if a.status == KEPT]

    @property
    def skipped(self) -> list[Action]:
        return [a for a in self.actions if a.status == SKIPPED]

    @property
    def freed_bytes(self) -> int:
        return sum(a.size for a in self.done)


def purge_quarantine(
    quarantine_dir: str,
    older_than: timedelta,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PurgeReport:
    """Delete quarantined files whose manifest ``time`` is before ``now - older_than``."""
    qdir = os.path.abspath(quarantine_dir)
    if not os.path.isdir(qdir):
        raise RestoreError(f"not a folder: {qdir}")
    manifest = os.path.join(qdir, MANIFEST_NAME)
    entries = _read_manifest(manifest)
    cutoff = (now or datetime.now(timezone.utc)) - older_than
    report = PurgeReport(qdir, dry_run, cutoff)
    keep_lines: list[str] = []

    for line, record in entries:
        why = _valid(record)
        if why:
            report.actions.append(Action(line[:80], SKIPPED, 0, why))
            keep_lines.append(line)
            continue
        assert record is not None
        original, src = record["original"], record["quarantined"]

        def skip(reason: str) -> None:
            report.actions.append(Action(original, SKIPPED, 0, reason, dest=src))
            keep_lines.append(line)

        moment = _parse_time(record.get("time"))
        if moment is None:
            skip("manifest record has no valid 'time'")
            continue
        if moment >= cutoff:
            report.actions.append(Action(original, KEPT, record["size"], dest=src))
            keep_lines.append(line)
            continue
        if not os.path.isabs(src) or not _inside(src, qdir):
            skip(f"quarantined path {src} is outside the quarantine folder")
            continue
        if not os.path.lexists(src):
            skip(f"{src} is no longer in the quarantine")
            continue
        st, why = _check_regular(src, record["size"])
        if st is not None:
            why = _check_hash(src, record["sha256"])
        if why:
            skip(f"quarantined copy {why}")
            continue

        if dry_run:
            report.actions.append(Action(original, WOULD_PURGE, record["size"], dest=src))
            continue
        try:
            os.remove(src)
        except OSError as exc:
            skip(f"delete failed: {exc.strerror or exc}")
            continue
        report.actions.append(Action(original, PURGED, record["size"], dest=src))
        _prune_empty_dirs(qdir, src)

    if not dry_run and report.done:
        _rewrite_manifest(manifest, keep_lines)
    return report
