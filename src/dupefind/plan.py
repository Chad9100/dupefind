"""Build, write and load reviewable plan files.

A plan is a JSON document listing, for each duplicate group, the one copy to
keep and the copies to act on. Nothing is moved or deleted until the plan is
passed to ``dupefind apply``, so it can be reviewed (and edited) first.
"""

from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Sequence

from . import __version__
from .scanner import DupGroup, FileInfo, ScanResult

PLAN_FORMAT = 1
STRATEGIES = ("oldest", "newest", "shortest-path", "first-in")

_STRATEGY_KEYS = {
    "oldest": lambda f: f.mtime,
    "newest": lambda f: -f.mtime,
    "shortest-path": lambda f: len(f.path),
    "first-in": lambda f: f.root_index,
}


def parse_strategies(spec: str) -> list[str]:
    """Split a comma-separated ``--keep`` value into validated strategy names.

    Earlier names take priority; later ones only break ties.
    """
    names = [n.strip() for n in spec.split(",")]
    for n in names:
        if n not in _STRATEGY_KEYS:
            raise ValueError(f"unknown keep strategy: {n or '(empty)'}")
    return names


def keep_spec(spec: str) -> str:
    """argparse type for ``--keep``: validate and normalise 'a, b' to 'a,b'."""
    return ",".join(parse_strategies(spec))


_README = (
    "Review before running 'dupefind apply'. In each group the 'keep' file is "
    "left alone and every 'remove' file is moved to quarantine (or deleted with "
    "--delete). To spare a file, delete its line from 'remove' or delete the "
    "whole group. Every file is re-hashed before anything happens."
)


class PlanError(Exception):
    """The plan file is missing, unreadable or malformed."""


def path_matches(path: str, patterns: Sequence[str]) -> bool:
    """True if the file name or the full path (any separator) matches a glob."""
    name = os.path.basename(path)
    full = path.replace("\\", "/")
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(full, p) for p in patterns)


def choose_keep(
    files: Sequence[FileInfo],
    strategy: str,
    *,
    keep_patterns: Sequence[str] = (),
    remove_patterns: Sequence[str] = (),
) -> FileInfo:
    """Pick the copy to keep.

    oldest / newest use modification time; shortest-path uses path length;
    first-in prefers the earliest PATH given on the command line. A
    comma-separated list such as ``first-in,oldest`` applies each in turn,
    later ones only breaking ties of earlier ones. Remaining ties fall
    back to PATH order and then alphabetical path order, so the choice is
    deterministic.

    ``keep_patterns`` narrow the candidates to copies matching a glob, then
    ``remove_patterns`` drop copies matching a glob; a filter that would leave
    no candidate is ignored, so one copy is always kept.
    """
    if keep_patterns:
        files = [f for f in files if path_matches(f.path, keep_patterns)] or list(files)
    if remove_patterns:
        files = [f for f in files if not path_matches(f.path, remove_patterns)] or list(files)
    parts = [_STRATEGY_KEYS[n] for n in parse_strategies(strategy)]
    return min(files, key=lambda f: tuple(p(f) for p in parts) + (f.root_index, f.path))


def _group_entry(
    group: DupGroup, strategy: str, keep_patterns: Sequence[str], remove_patterns: Sequence[str]
) -> dict:
    keep = choose_keep(
        group.files, strategy, keep_patterns=keep_patterns, remove_patterns=remove_patterns
    )
    return {
        "size": group.size,
        "sha256": group.sha256,
        "keep": keep.path,
        "remove": [f.path for f in group.files if f is not keep],
    }


def build_plan(
    result: ScanResult,
    strategy: str,
    *,
    now: datetime | None = None,
    keep_patterns: Sequence[str] = (),
    remove_patterns: Sequence[str] = (),
) -> dict:
    now = now or datetime.now(timezone.utc)
    groups = [_group_entry(g, strategy, keep_patterns, remove_patterns) for g in result.groups]
    return {
        "dupefind_plan": PLAN_FORMAT,
        "_readme": _README,
        "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dupefind_version": __version__,
        "keep_strategy": strategy,
        "roots": list(result.roots),
        "summary": {
            "groups": len(groups),
            "files_to_remove": sum(len(g["remove"]) for g in groups),
            "reclaimable_bytes": sum(g["size"] * len(g["remove"]) for g in groups),
        },
        "groups": groups,
    }


def write_plan(plan: dict, path: str, *, force: bool = False) -> None:
    """Write ``plan`` as JSON atomically. Refuses to overwrite unless ``force``."""
    path = os.path.abspath(path)
    if os.path.exists(path) and not force:
        raise FileExistsError(f"{path} already exists (use --force to overwrite)")
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".dupefind-plan-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(plan, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise PlanError(message)


def validate_plan(plan: object) -> dict:
    """Check the structure of a loaded plan and return it."""
    _require(isinstance(plan, dict), "plan must be a JSON object")
    assert isinstance(plan, dict)
    version = plan.get("dupefind_plan")
    _require(version is not None, "not a dupefind plan (missing 'dupefind_plan')")
    _require(version == PLAN_FORMAT, f"unsupported plan format {version!r}")
    groups = plan.get("groups")
    _require(isinstance(groups, list), "'groups' must be a list")
    for i, g in enumerate(groups, 1):
        where = f"group {i}"
        _require(isinstance(g, dict), f"{where} must be an object")
        size = g.get("size")
        _require(
            isinstance(size, int) and not isinstance(size, bool) and size >= 0,
            f"{where}: 'size' must be a non-negative integer",
        )
        digest = g.get("sha256")
        _require(
            isinstance(digest, str)
            and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest),
            f"{where}: 'sha256' must be a 64-character lowercase hex digest",
        )
        _require(isinstance(g.get("keep"), str) and g["keep"], f"{where}: 'keep' must be a path")
        remove = g.get("remove")
        _require(
            isinstance(remove, list) and all(isinstance(p, str) and p for p in remove),
            f"{where}: 'remove' must be a list of paths",
        )
    return plan


def load_plan(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise PlanError(f"cannot read plan {path}: {exc.strerror or exc}") from None
    except json.JSONDecodeError as exc:
        raise PlanError(f"plan {path} is not valid JSON: {exc}") from None
    return validate_plan(data)
