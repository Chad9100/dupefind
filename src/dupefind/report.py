"""Human and JSON output for scan results, plus size parsing/formatting."""

from __future__ import annotations

import csv
import io
import re

from . import __version__
from .scanner import ScanResult

_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)


def parse_size(text: str) -> int:
    """Parse ``"10"``, ``"4K"``, ``"1.5MB"``, ``"2GiB"`` into bytes (1K = 1024)."""
    m = _SIZE_RE.match(text)
    if not m:
        raise ValueError(f"invalid size: {text!r} (examples: 100, 4K, 1.5M, 2GiB)")
    number, unit = m.groups()
    return int(float(number) * _UNITS[unit.lower()])


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        value /= 1024
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


SORT_KEYS = ("wasted", "size", "count", "path")


def sort_groups(groups: list, key: str) -> list:
    """Return groups ordered by ``key``; ties fall back to the first path."""
    if key == "wasted":
        return sorted(groups, key=lambda g: (-g.wasted, g.files[0].path))
    if key == "size":
        return sorted(groups, key=lambda g: (-g.size, g.files[0].path))
    if key == "count":
        return sorted(groups, key=lambda g: (-len(g.files), g.files[0].path))
    if key == "path":
        return sorted(groups, key=lambda g: g.files[0].path)
    raise ValueError(f"unknown sort key: {key!r} (choose from {', '.join(SORT_KEYS)})")


def render_report(result: ScanResult, *, limit: int | None = None) -> str:
    lines = [
        f"Scanned {_plural(result.files_scanned, 'file')} "
        f"({format_size(result.bytes_scanned)}) in {_plural(len(result.roots), 'root')}"
    ]
    if not result.groups:
        lines.append("No duplicates found.")
    else:
        lines.append(
            f"Found {_plural(len(result.groups), 'duplicate group')}: "
            f"{_plural(result.duplicate_files, 'file')}, "
            f"{_plural(result.redundant_copies, 'redundant copy', 'redundant copies')}"
        )
        lines.append(f"Wasted space: {format_size(result.wasted)}")
        shown = result.groups if limit is None else result.groups[:limit]
        for i, group in enumerate(shown, 1):
            lines.append("")
            lines.append(
                f"[{i}] {format_size(group.size)} x {len(group.files)} "
                f"(wasted {format_size(group.wasted)})  sha256 {group.sha256[:12]}"
            )
            for f in group.files:
                lines.append(f"    {f.path}")
        hidden = len(result.groups) - len(shown)
        if hidden > 0:
            lines.append("")
            lines.append(f"... and {_plural(hidden, 'more group')} (use --limit 0 to show all)")
    if result.hardlinks_skipped:
        lines.append("")
        lines.append(
            f"Note: {_plural(result.hardlinks_skipped, 'hard link')} to already-listed "
            "files ignored (they use no extra space)."
        )
    if result.errors:
        lines.append("")
        lines.append(f"{_plural(len(result.errors), 'path')} could not be read:")
        for err in result.errors:
            lines.append(f"    {err.path}: {err.message}")
    return "\n".join(lines)


def result_to_dict(result: ScanResult) -> dict:
    return {
        "dupefind_version": __version__,
        "roots": result.roots,
        "files_scanned": result.files_scanned,
        "bytes_scanned": result.bytes_scanned,
        "duplicate_groups": len(result.groups),
        "redundant_copies": result.redundant_copies,
        "wasted_bytes": result.wasted,
        "hardlinks_skipped": result.hardlinks_skipped,
        "groups": [
            {
                "size": g.size,
                "sha256": g.sha256,
                "wasted_bytes": g.wasted,
                "files": [{"path": f.path, "mtime": f.mtime} for f in g.files],
            }
            for g in result.groups
        ],
        "errors": [{"path": e.path, "error": e.message} for e in result.errors],
    }


CSV_COLUMNS = ("group", "size_bytes", "sha256", "copies", "wasted_bytes", "mtime", "path")


def render_csv(result: ScanResult) -> str:
    """One row per duplicate file, groups in their current order (header included)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for i, g in enumerate(result.groups, 1):
        for f in g.files:
            writer.writerow([i, g.size, g.sha256, len(g.files), g.wasted, f.mtime, f.path])
    return buf.getvalue()
