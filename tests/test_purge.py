import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from dupefind.apply import MANIFEST_NAME, SKIPPED, apply_plan
from dupefind.cli import main
from dupefind.plan import build_plan
from dupefind.purge import KEPT, PURGED, WOULD_PURGE, parse_age, purge_quarantine
from dupefind.restore import RestoreError
from dupefind.scanner import QUARANTINE_MARKER, find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def manifest_records(q):
    text = (q / MANIFEST_NAME).read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def set_times(q, times):
    """Rewrite the manifest so record i gets ``times[i]``."""
    records = manifest_records(q)
    for record, moment in zip(records, times):
        record["time"] = moment
    (q / MANIFEST_NAME).write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return records


def quarantined(tmp_path):
    """Make three identical files, quarantine two, return (q, keep, {original: dest})."""
    data = tmp_path / "data"
    keep = write(data / "a.txt", "duplicate content")
    e1 = write(data / "b.txt", "duplicate content")
    e2 = write(data / "sub" / "c.txt", "duplicate content")
    os.utime(keep, (1000, 1000))
    os.utime(e1, (2000, 2000))
    os.utime(e2, (3000, 3000))
    q = tmp_path / "q"
    plan = build_plan(find_duplicates([str(data)]), "oldest")
    report = apply_plan(plan, quarantine_dir=str(q))
    return q, keep, {a.path: a.dest for a in report.actions}


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
OLD = "2026-09-01T08:00:00Z"
RECENT = "2026-11-09T08:00:00Z"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("30d", timedelta(days=30)),
        ("12h", timedelta(hours=12)),
        ("2w", timedelta(weeks=2)),
        ("90m", timedelta(minutes=90)),
        ("1.5D", timedelta(hours=36)),
        ("0d", timedelta(0)),
    ],
)
def test_parse_age(text, expected):
    assert parse_age(text) == expected


@pytest.mark.parametrize("text", ["30", "d", "-1d", "3y", "", "1 day"])
def test_parse_age_rejects(text):
    with pytest.raises(ValueError):
        parse_age(text)


def test_purge_deletes_only_old_records(tmp_path):
    q, keep, dests = quarantined(tmp_path)
    old, recent = set_times(q, [OLD, RECENT])
    report = purge_quarantine(str(q), timedelta(days=30), now=NOW)
    assert [a.status for a in report.actions] == [PURGED, KEPT]
    assert report.freed_bytes == len("duplicate content")
    assert not os.path.exists(old["quarantined"])
    assert os.path.isfile(recent["quarantined"])
    assert keep.exists()
    assert [r["original"] for r in manifest_records(q)] == [recent["original"]]


def test_purge_all_removes_empty_folders(tmp_path):
    q, _, dests = quarantined(tmp_path)
    set_times(q, [OLD, OLD])
    report = purge_quarantine(str(q), timedelta(days=1), now=NOW)
    assert [a.status for a in report.actions] == [PURGED, PURGED]
    assert manifest_records(q) == []
    assert sorted(os.listdir(q)) == sorted([QUARANTINE_MARKER, MANIFEST_NAME])


def test_purge_uses_real_apply_time(tmp_path):
    q, _, dests = quarantined(tmp_path)
    report = purge_quarantine(str(q), timedelta(days=30))
    assert [a.status for a in report.actions] == [KEPT, KEPT]
    later = datetime.now(timezone.utc) + timedelta(days=31)
    report = purge_quarantine(str(q), timedelta(days=30), now=later)
    assert [a.status for a in report.actions] == [PURGED, PURGED]


def test_purge_dry_run_changes_nothing(tmp_path):
    q, _, dests = quarantined(tmp_path)
    set_times(q, [OLD, OLD])
    before = (q / MANIFEST_NAME).read_bytes()
    report = purge_quarantine(str(q), timedelta(days=30), dry_run=True, now=NOW)
    assert [a.status for a in report.actions] == [WOULD_PURGE, WOULD_PURGE]
    assert all(os.path.isfile(d) for d in dests.values())
    assert (q / MANIFEST_NAME).read_bytes() == before


def test_purge_skips_changed_file(tmp_path):
    q, _, dests = quarantined(tmp_path)
    first, second = set_times(q, [OLD, OLD])
    write(type(q)(first["quarantined"]), "edited inside the quarantine!")
    report = purge_quarantine(str(q), timedelta(days=30), now=NOW)
    assert [a.status for a in report.actions] == [SKIPPED, PURGED]
    assert "quarantined copy" in report.actions[0].detail
    assert os.path.isfile(first["quarantined"])
    assert [r["original"] for r in manifest_records(q)] == [first["original"]]


def test_purge_never_deletes_outside_quarantine(tmp_path):
    q, keep, _ = quarantined(tmp_path)
    records = set_times(q, [OLD, OLD])
    records[0]["quarantined"] = str(keep)  # hand-edited to point at the kept copy
    (q / MANIFEST_NAME).write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    report = purge_quarantine(str(q), timedelta(days=30), now=NOW)
    assert report.actions[0].status == SKIPPED
    assert "outside the quarantine" in report.actions[0].detail
    assert keep.read_text() == "duplicate content"


def test_purge_skips_records_without_time(tmp_path):
    q, _, dests = quarantined(tmp_path)
    set_times(q, ["not a date", OLD])
    report = purge_quarantine(str(q), timedelta(days=30), now=NOW)
    assert [a.status for a in report.actions] == [SKIPPED, PURGED]
    assert "'time'" in report.actions[0].detail


def test_purge_not_a_quarantine(tmp_path):
    with pytest.raises(RestoreError):
        purge_quarantine(str(tmp_path / "missing"), timedelta(days=1))
    with pytest.raises(RestoreError):
        purge_quarantine(str(tmp_path), timedelta(days=1))


def test_cli_purge(tmp_path, capsys):
    q, _, dests = quarantined(tmp_path)
    set_times(q, [OLD, "2999-01-01T00:00:00Z"])
    assert main(["purge", str(q), "--older-than", "30d", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would purge" in out
    assert "Dry run: would have purged 1 file" in out
    assert "kept 1 newer" in out
    assert main(["purge", str(q), "--older-than", "30d"]) == 0
    out = capsys.readouterr().out
    assert "Purged 1 file" in out
    assert len(manifest_records(q)) == 1


def test_cli_purge_requires_valid_age(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["purge", str(tmp_path), "--older-than", "soon"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        main(["purge", str(tmp_path)])
    assert main(["purge", str(tmp_path), "--older-than", "1d"]) == 2
    assert "no manifest.jsonl" in capsys.readouterr().err
