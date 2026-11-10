"""Command-line interface: ``dupefind scan | plan | apply | restore | purge``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from . import __version__
from .apply import MOVED, SKIPPED, WOULD_MOVE, apply_plan
from .cache import HashCache
from .progress import ProgressLine, stream_is_tty
from .plan import STRATEGIES, PlanError, build_plan, keep_spec, load_plan, load_scan, write_plan


def _keep_type(value: str) -> str:
    try:
        return keep_spec(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{exc} (choose from {', '.join(STRATEGIES)})") from None
from .report import format_size, parse_size, render_report, result_to_dict
from .purge import KEPT, parse_age, purge_quarantine
from .restore import RestoreError, restore_quarantine
from .scanner import find_duplicates

DEFAULT_PLAN = "dupefind-plan.json"
DEFAULT_QUARANTINE = "dupefind-quarantine"


def _size_arg(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _jobs_arg(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _age_arg(text: str):
    try:
        return parse_age(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _scan_options() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("paths", nargs="*", metavar="PATH", help="files or folders to scan")
    p.add_argument(
        "--min-size",
        type=_size_arg,
        default=1,
        metavar="SIZE",
        help="ignore files smaller than SIZE, e.g. 100, 4K, 1.5M (default: 1 byte, "
        "so empty files are skipped; use 0 to include them)",
    )
    p.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="only consider files whose name or relative path matches GLOB (repeatable)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files and folders whose name or relative path matches GLOB (repeatable)",
    )
    p.add_argument(
        "-q", "--quiet", action="store_true",
        help="no progress line on stderr (it is shown only when stderr is a terminal)",
    )
    p.add_argument(
        "-j", "--jobs", type=_jobs_arg, default=1, metavar="N",
        help="hash up to N files at once in threads (default: 1); can speed up "
        "scans on SSDs and network drives, results are the same",
    )
    p.add_argument(
        "--cache", metavar="FILE", default=None,
        help="remember file hashes in FILE (created if missing) so repeat scans "
        "skip files whose size and modification time are unchanged",
    )
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupefind",
        description="Find duplicate files, review a plan, then quarantine or delete the extras.",
    )
    parser.add_argument("--version", action="version", version=f"dupefind {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    common = _scan_options()

    scan = sub.add_parser(
        "scan", parents=[common], help="report duplicate files (read-only)",
        description="Report groups of identical files. Never modifies anything.",
    )
    scan.add_argument("--json", action="store_true", help="print machine-readable JSON")
    scan.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="show at most N groups in the human report (0 = all; default 20)",
    )

    plan = sub.add_parser(
        "plan", parents=[common], help="write a reviewable plan of what to remove",
        description="Scan, choose one copy to keep per group, and write the plan to a "
        "JSON file. Nothing is moved or deleted.",
    )
    plan.add_argument(
        "--keep", type=_keep_type, default="oldest", metavar="STRATEGY[,STRATEGY...]",
        help="which copy to keep: oldest/newest by modification time, shortest-path, "
        "or first-in (the copy under the earliest PATH argument). Comma-separate "
        "several to use later ones as tie-breakers, e.g. first-in,oldest. "
        "Default: oldest",
    )
    plan.add_argument(
        "--keep-pattern", action="append", default=[], metavar="GLOB",
        help="prefer keeping copies whose name or path matches GLOB (repeatable)",
    )
    plan.add_argument(
        "--remove-pattern", action="append", default=[], metavar="GLOB",
        help="prefer removing copies whose name or path matches GLOB (repeatable)",
    )
    plan.add_argument(
        "-o", "--output", default=DEFAULT_PLAN, metavar="FILE",
        help=f"plan file to write (default: {DEFAULT_PLAN})",
    )
    plan.add_argument("--force", action="store_true", help="overwrite an existing plan file")
    plan.add_argument(
        "--from-scan", metavar="SCAN_JSON", default=None,
        help="build the plan from saved 'dupefind scan --json' output instead of "
        "scanning (PATH and scan options are not used)",
    )

    apply = sub.add_parser(
        "apply", help="carry out a plan (quarantine by default)",
        description="Re-verify every file in PLAN, then move the extra copies to a "
        "quarantine folder, or delete them with --delete.",
    )
    apply.add_argument("plan", metavar="PLAN", help="plan file written by 'dupefind plan'")
    apply.add_argument(
        "--quarantine", metavar="DIR",
        help=f"where to move extra copies (default: {DEFAULT_QUARANTINE}/ next to the plan)",
    )
    apply.add_argument("--delete", action="store_true", help="delete extra copies instead of moving")
    apply.add_argument("--dry-run", action="store_true", help="verify and show what would happen")

    restore = sub.add_parser(
        "restore", help="move quarantined files back to where they came from",
        description="Read manifest.jsonl in QUARANTINE_DIR, re-verify each file and move it "
        "back to its original path. A file that now exists at the original path is never "
        "overwritten.",
    )
    restore.add_argument(
        "quarantine", metavar="QUARANTINE_DIR",
        help="quarantine folder written by 'dupefind apply'",
    )
    restore.add_argument("--dry-run", action="store_true", help="verify and show what would happen")

    purge = sub.add_parser(
        "purge", help="permanently delete files quarantined long ago",
        description="Delete files in QUARANTINE_DIR whose manifest.jsonl record is older "
        "than AGE. Each file is re-verified first; files not in the manifest are never "
        "touched. This cannot be undone.",
    )
    purge.add_argument(
        "quarantine", metavar="QUARANTINE_DIR",
        help="quarantine folder written by 'dupefind apply'",
    )
    purge.add_argument(
        "--older-than", type=_age_arg, required=True, metavar="AGE",
        help="only purge files quarantined more than AGE ago: 90m, 12h, 30d, 2w",
    )
    purge.add_argument("--dry-run", action="store_true", help="verify and show what would happen")
    return parser


def _files(n: int) -> str:
    return f"{n:,} file" if n == 1 else f"{n:,} files"


def _warn_errors(result) -> None:
    for err in result.errors:
        print(f"dupefind: warning: {err.path}: {err.message}", file=sys.stderr)


def _run_scan(args):
    progress = None
    if not args.quiet and stream_is_tty(sys.stderr):
        progress = ProgressLine(sys.stderr)
    cache = None
    if args.cache:
        cache = HashCache(args.cache)
        if cache.warning:
            print(f"dupefind: warning: {cache.warning}", file=sys.stderr)
    try:
        result = find_duplicates(
            args.paths, include=args.include, exclude=args.exclude,
            min_size=args.min_size, progress=progress, jobs=args.jobs, cache=cache,
        )
    finally:
        if progress is not None:
            progress.finish()
    if cache is not None:
        try:
            cache.save()
        except OSError as exc:
            print(f"dupefind: warning: could not save cache {cache.path}: {exc}",
                  file=sys.stderr)
    return result


def _cmd_scan(args) -> int:
    if not args.paths:
        print("dupefind: error: scan needs at least one PATH", file=sys.stderr)
        return 2
    result = _run_scan(args)
    if args.json:
        print(json.dumps(result_to_dict(result), indent=2, ensure_ascii=False))
    else:
        limit = None if args.limit <= 0 else args.limit
        print(render_report(result, limit=limit))
    return 0


def _cmd_plan(args) -> int:
    if args.from_scan:
        if args.paths:
            print("dupefind: error: PATH cannot be combined with --from-scan", file=sys.stderr)
            return 2
        try:
            result = load_scan(args.from_scan)
        except PlanError as exc:
            print(f"dupefind: error: {exc}", file=sys.stderr)
            return 2
    else:
        if not args.paths:
            print("dupefind: error: plan needs PATH or --from-scan", file=sys.stderr)
            return 2
        result = _run_scan(args)
    _warn_errors(result)
    if not result.groups:
        print(f"Scanned {result.files_scanned:,} files: no duplicates found; no plan written.")
        return 0
    plan = build_plan(
        result, args.keep,
        keep_patterns=args.keep_pattern, remove_patterns=args.remove_pattern,
    )
    try:
        write_plan(plan, args.output, force=args.force)
    except FileExistsError as exc:
        print(f"dupefind: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"dupefind: error: cannot write {args.output}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    s = plan["summary"]
    print(
        f"Wrote {os.path.abspath(args.output)}\n"
        f"  {s['groups']:,} groups, {s['files_to_remove']:,} files to remove "
        f"(keep: {args.keep}), {format_size(s['reclaimable_bytes'])} reclaimable\n"
        f"Review it, then run: dupefind apply {args.output} --dry-run"
    )
    return 0


def _cmd_apply(args) -> int:
    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"dupefind: error: {exc}", file=sys.stderr)
        return 2
    qdir = args.quarantine
    if not args.delete and not qdir:
        qdir = os.path.join(os.path.dirname(os.path.abspath(args.plan)), DEFAULT_QUARANTINE)
    report = apply_plan(plan, delete=args.delete, quarantine_dir=qdir, dry_run=args.dry_run)

    width = max((len(a.status) for a in report.actions), default=0)
    for a in report.actions:
        line = f"{a.status:<{width}}  {a.path}"
        if a.status in (MOVED, WOULD_MOVE) and a.dest:
            line += f" -> {a.dest}"
        elif a.status == SKIPPED:
            line += f": {a.detail}"
        print(line)

    verb = "deleted" if args.delete else "moved"
    if args.dry_run:
        summary = (
            f"Dry run: would have {verb} {_files(len(report.done))} "
            f"({format_size(report.reclaimed)})"
        )
    else:
        summary = f"{verb.capitalize()} {_files(len(report.done))} ({format_size(report.reclaimed)})"
    if report.skipped:
        summary += f", skipped {len(report.skipped):,}"
    if not args.delete and report.done and not args.dry_run:
        summary += f"\nQuarantine: {report.quarantine_dir}"
    if not report.actions:
        summary = "Plan has nothing to do."
    print(summary)
    return 1 if report.skipped else 0


def _cmd_restore(args) -> int:
    try:
        report = restore_quarantine(args.quarantine, dry_run=args.dry_run)
    except RestoreError as exc:
        print(f"dupefind: error: {exc}", file=sys.stderr)
        return 2

    width = max((len(a.status) for a in report.actions), default=0)
    for a in report.actions:
        line = f"{a.status:<{width}}  {a.path}"
        if a.status == SKIPPED:
            line += f": {a.detail}"
        print(line)

    n = f"{_files(len(report.done))} ({format_size(report.restored_bytes)})"
    summary = f"Dry run: would have restored {n}" if args.dry_run else f"Restored {n}"
    if report.skipped:
        summary += f", skipped {len(report.skipped):,}"
    if not report.actions:
        summary = "Quarantine manifest is empty; nothing to restore."
    print(summary)
    return 1 if report.skipped else 0


def _cmd_purge(args) -> int:
    try:
        report = purge_quarantine(args.quarantine, args.older_than, dry_run=args.dry_run)
    except RestoreError as exc:
        print(f"dupefind: error: {exc}", file=sys.stderr)
        return 2

    shown = [a for a in report.actions if a.status != KEPT]
    width = max((len(a.status) for a in shown), default=0)
    for a in shown:
        line = f"{a.status:<{width}}  {a.dest or a.path}"
        if a.status == SKIPPED:
            line += f": {a.detail}"
        print(line)

    n = f"{_files(len(report.done))} ({format_size(report.freed_bytes)})"
    summary = f"Dry run: would have purged {n}" if args.dry_run else f"Purged {n}"
    if report.kept:
        summary += f", kept {len(report.kept):,} newer"
    if report.skipped:
        summary += f", skipped {len(report.skipped):,}"
    if not report.actions:
        summary = "Quarantine manifest is empty; nothing to purge."
    print(summary)
    return 1 if report.skipped else 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    handlers = {
        "scan": _cmd_scan,
        "plan": _cmd_plan,
        "apply": _cmd_apply,
        "restore": _cmd_restore,
        "purge": _cmd_purge,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("dupefind: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
