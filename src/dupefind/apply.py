"""Carry out a reviewed plan: quarantine (default) or delete the extra copies.

Before touching anything, every file is checked again: the kept copy must
still exist with the planned hash, and each extra copy must still be a regular
file (not a symlink), must not be the same physical file as the kept copy, and
must still hash to the planned SHA-256. Anything that fails a check is skipped
and reported; the rest of the plan still runs.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from .scanner import QUARANTINE_MARKER, hash_file, is_link

MANIFEST_NAME = "manifest.jsonl"

MOVED = "moved"
DELETED = "deleted"
WOULD_MOVE = "would move"
WOULD_DELETE = "would delete"
LINKED = "linked"
WOULD_LINK = "would link"
SKIPPED = "skipped"


@dataclass
class Action:
    path: str
    status: str
    size: int = 0
    detail: str = ""
    dest: str | None = None


@dataclass
class ApplyReport:
    mode: str
    dry_run: bool
    quarantine_dir: str | None
    actions: list[Action] = field(default_factory=list)

    @property
    def done(self) -> list[Action]:
        return [a for a in self.actions if a.status != SKIPPED]

    @property
    def skipped(self) -> list[Action]:
        return [a for a in self.actions if a.status == SKIPPED]

    @property
    def reclaimed(self) -> int:
        return sum(a.size for a in self.done)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _mirror(quarantine_dir: str, path: str) -> str:
    drive, rest = os.path.splitdrive(path)
    parts = [p for p in drive.replace(":", "").replace("\\", "/").split("/") if p]
    rest_parts = [p for p in rest.replace("\\", "/").split("/") if p]
    return os.path.join(quarantine_dir, *parts, *rest_parts)


def _root_label(root: str) -> str:
    name = os.path.basename(root.rstrip("\\/"))
    if name:
        return name
    drive = os.path.splitdrive(root)[0].replace(":", "").strip("\\/")
    return drive.replace("\\", "_").replace("/", "_") or "root"


def quarantine_dest(quarantine_dir: str, path: str, roots: Sequence[str] = ()) -> str:
    """Where ``path`` goes inside the quarantine folder.

    A file under one of the scanned roots keeps its path relative to that
    root, below a folder named after the root: ``photos/2024/a.jpg`` scanned
    from root ``photos`` becomes ``<q>/photos/2024/a.jpg``. This keeps
    quarantine paths short (Windows still has a 260-character limit in many
    setups). Files outside every root mirror their full absolute path, e.g.
    ``/data/a.txt`` becomes ``<q>/data/a.txt`` (a Windows drive letter
    becomes a folder named ``C`` and so on).
    """
    path = os.path.abspath(path)
    for root in sorted((os.path.abspath(r) for r in roots), key=len):
        try:
            rel = os.path.relpath(path, root)
        except ValueError:  # different drive on Windows
            continue
        if rel == os.curdir:
            return os.path.join(quarantine_dir, os.path.basename(path))
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            continue
        return os.path.join(quarantine_dir, _root_label(root), rel)
    return _mirror(quarantine_dir, path)


def _unique(dest: str) -> str:
    if not os.path.lexists(dest):
        return dest
    n = 1
    while os.path.lexists(f"{dest}~{n}"):
        n += 1
    return f"{dest}~{n}"


def _prepare_quarantine(qdir: str) -> None:
    os.makedirs(qdir, exist_ok=True)
    marker = os.path.join(qdir, QUARANTINE_MARKER)
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("Files moved here by dupefind apply. See manifest.jsonl.\n")


def _append_manifest(qdir: str, record: dict) -> None:
    # Written after every move so the record survives an interrupted run.
    with open(os.path.join(qdir, MANIFEST_NAME), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _check_regular(path: str, size: int) -> tuple[os.stat_result | None, str]:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, "no longer exists"
    except OSError as exc:
        return None, f"cannot stat: {exc.strerror or exc}"
    if is_link(st):
        return None, "is now a symlink"
    if not stat.S_ISREG(st.st_mode):
        return None, "is no longer a regular file"
    if st.st_size != size:
        return None, f"size changed ({st.st_size} bytes, plan says {size})"
    return st, ""


def _check_hash(path: str, digest: str) -> str:
    try:
        actual = hash_file(path)
    except OSError as exc:
        return f"cannot read: {exc.strerror or exc}"
    if actual != digest:
        return "content changed since the plan was made"
    return ""


def _replace_with_link(keep: str, path: str, kind: str) -> str:
    """Replace ``path`` by a link to ``keep``; return an error text or ''.

    The link is created under a temporary name next to ``path`` and then
    renamed over it, so ``path`` is never missing and a failure leaves the
    original file untouched.
    """
    tmp = f"{path}.dupefind-link-{os.getpid()}"
    try:
        if kind == "hardlink":
            os.link(keep, tmp)
        else:
            os.symlink(keep, tmp)
    except NotImplementedError:
        return f"this system cannot create {kind}s"
    except OSError as exc:
        return f"cannot create {kind} (file system or permissions may not support it): {exc.strerror or exc}"
    try:
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return f"{kind} replace failed: {exc.strerror or exc}"
    return ""


def apply_plan(
    plan: dict,
    *,
    delete: bool = False,
    quarantine_dir: str | None = None,
    dry_run: bool = False,
    link: str | None = None,
) -> ApplyReport:
    """Apply a validated plan (see :func:`dupefind.plan.load_plan`).

    ``link`` is ``"hardlink"`` or ``"symlink"`` to replace each extra copy with
    a link to the kept copy instead of quarantining or deleting it.
    """
    if link not in (None, "hardlink", "symlink"):
        raise ValueError(f"unknown link mode: {link!r}")
    if link and delete:
        raise ValueError("link and delete cannot be combined")
    if not delete and not link and not quarantine_dir:
        raise ValueError("quarantine_dir is required unless delete=True or link is set")
    qdir = None if (delete or link) else os.path.abspath(quarantine_dir)  # type: ignore[arg-type]
    report = ApplyReport(link or ("delete" if delete else "quarantine"), dry_run, qdir)

    roots = [r for r in plan.get("roots", []) if isinstance(r, str) and os.path.isabs(r)]
    keeps = {_norm(g["keep"]) for g in plan["groups"]}
    handled: set[str] = set()

    for group in plan["groups"]:
        size, digest, keep = group["size"], group["sha256"], group["keep"]

        def skip(path: str, why: str) -> None:
            report.actions.append(Action(path, SKIPPED, 0, why))

        if not os.path.isabs(keep):
            for p in group["remove"]:
                skip(p, f"kept copy path is not absolute: {keep}")
            continue
        keep_st, why = _check_regular(keep, size)
        if keep_st is not None:
            why = _check_hash(keep, digest)
        if why:
            for p in group["remove"]:
                skip(p, f"kept copy {keep} {why}; group left alone")
            continue

        for path in group["remove"]:
            key = _norm(path)
            if not os.path.isabs(path):
                skip(path, "path is not absolute")
                continue
            if key in keeps:
                skip(path, "is the kept copy of a group")
                continue
            if key in handled:
                skip(path, "listed more than once in the plan")
                continue
            handled.add(key)
            if qdir and (key == _norm(qdir) or key.startswith(_norm(qdir) + os.sep)):
                skip(path, "is inside the quarantine folder")
                continue
            st, why = _check_regular(path, size)
            if st is None:
                skip(path, why)
                continue
            if os.path.samestat(st, keep_st) and st.st_ino:
                skip(path, "is the same physical file as the kept copy")
                continue
            why = _check_hash(path, digest)
            if why:
                skip(path, why)
                continue

            if link:
                if link == "hardlink" and st.st_dev != keep_st.st_dev:
                    skip(path, "is on a different volume than the kept copy; cannot hard link")
                    continue
                if dry_run:
                    report.actions.append(Action(path, WOULD_LINK, size, dest=keep))
                    continue
                err = _replace_with_link(keep, path, link)
                if err:
                    skip(path, err)
                    continue
                report.actions.append(Action(path, LINKED, size, dest=keep))
            elif delete:
                if dry_run:
                    report.actions.append(Action(path, WOULD_DELETE, size))
                    continue
                try:
                    os.remove(path)
                except OSError as exc:
                    skip(path, f"delete failed: {exc.strerror or exc}")
                    continue
                report.actions.append(Action(path, DELETED, size))
            else:
                dest = _unique(quarantine_dest(qdir, path, roots))  # type: ignore[arg-type]
                if dry_run:
                    report.actions.append(Action(path, WOULD_MOVE, size, dest=dest))
                    continue
                try:
                    _prepare_quarantine(qdir)  # type: ignore[arg-type]
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(path, dest)
                except OSError as exc:
                    skip(path, f"move failed: {exc.strerror or exc}")
                    continue
                report.actions.append(Action(path, MOVED, size, dest=dest))
                _append_manifest(
                    qdir,  # type: ignore[arg-type]
                    {
                        "original": path,
                        "quarantined": dest,
                        "size": size,
                        "sha256": digest,
                        "kept": keep,
                        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                )
    return report
