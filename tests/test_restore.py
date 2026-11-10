import json
import os

import pytest

from dupefind.apply import MANIFEST_NAME, SKIPPED, apply_plan
from dupefind.cli import main
from dupefind.plan import build_plan
from dupefind.restore import (
    RESTORED,
    WOULD_RESTORE,
    RestoreError,
    restore_quarantine,
)
from dupefind.scanner import QUARANTINE_MARKER, find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def quarantined(tmp_path):
    """Make three identical files, quarantine two of them, return paths."""
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
    assert not e1.exists() and not e2.exists()
    return q, keep, e1, e2, {a.path: a.dest for a in report.actions}


def manifest_records(q):
    text = (q / MANIFEST_NAME).read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_restore_moves_files_back(tmp_path):
    q, keep, e1, e2, dests = quarantined(tmp_path)
    report = restore_quarantine(str(q))
    assert [a.status for a in report.actions] == [RESTORED, RESTORED]
    assert e1.read_text() == e2.read_text() == "duplicate content"
    assert keep.exists()
    assert not any(os.path.exists(d) for d in dests.values())
    assert report.restored_bytes == 2 * len("duplicate content")
    assert manifest_records(q) == []
    # Emptied subfolders are removed; the marker and manifest stay.
    assert sorted(os.listdir(q)) == sorted([QUARANTINE_MARKER, MANIFEST_NAME])


def test_restore_dry_run_changes_nothing(tmp_path):
    q, _, e1, e2, dests = quarantined(tmp_path)
    before = (q / MANIFEST_NAME).read_bytes()
    report = restore_quarantine(str(q), dry_run=True)
    assert [a.status for a in report.actions] == [WOULD_RESTORE, WOULD_RESTORE]
    assert not e1.exists() and not e2.exists()
    assert all(os.path.isfile(d) for d in dests.values())
    assert (q / MANIFEST_NAME).read_bytes() == before


def test_restore_never_overwrites(tmp_path):
    q, _, e1, e2, dests = quarantined(tmp_path)
    write(e1, "new file written after apply")
    report = restore_quarantine(str(q))
    by_path = {a.path: a for a in report.actions}
    assert by_path[str(e1)].status == SKIPPED
    assert "not overwriting" in by_path[str(e1)].detail
    assert by_path[str(e2)].status == RESTORED
    assert e1.read_text() == "new file written after apply"
    assert os.path.isfile(dests[str(e1)])
    # Only the skipped file is still listed in the manifest.
    assert [r["original"] for r in manifest_records(q)] == [str(e1)]


def test_restore_twice_is_harmless(tmp_path):
    q, _, e1, e2, _ = quarantined(tmp_path)
    restore_quarantine(str(q))
    report = restore_quarantine(str(q))
    assert report.actions == []
    assert e1.exists() and e2.exists()


def test_changed_quarantined_file_is_skipped(tmp_path):
    q, _, e1, e2, dests = quarantined(tmp_path)
    with open(dests[str(e1)], "wb") as fh:
        fh.write(b"duplicate CONTENT")  # same size, different bytes
    report = restore_quarantine(str(q))
    by_path = {a.path: a for a in report.actions}
    assert by_path[str(e1)].status == SKIPPED
    assert "content changed" in by_path[str(e1)].detail
    assert not e1.exists()
    assert e2.exists()


def test_missing_quarantined_file_is_skipped(tmp_path):
    q, _, e1, e2, dests = quarantined(tmp_path)
    os.remove(dests[str(e1)])
    report = restore_quarantine(str(q))
    statuses = {a.path: a.status for a in report.actions}
    assert statuses == {str(e1): SKIPPED, str(e2): RESTORED}


def test_manifest_pointing_outside_quarantine_is_refused(tmp_path):
    q, _, e1, e2, _ = quarantined(tmp_path)
    outside = write(tmp_path / "elsewhere" / "x.txt", "duplicate content")
    records = manifest_records(q)
    records[0]["quarantined"] = str(outside)
    records.append("not a record")
    lines = [json.dumps(r) for r in records] + ["{broken json"]
    (q / MANIFEST_NAME).write_text("\n".join(lines) + "\n", "utf-8")
    report = restore_quarantine(str(q))
    skipped = report.skipped
    assert len(skipped) == 3
    assert any("outside the quarantine" in a.detail for a in skipped)
    assert outside.exists()  # never moved
    assert len(report.done) == 1
    # Unusable lines are kept in the manifest, untouched.
    remaining = (q / MANIFEST_NAME).read_text("utf-8").splitlines()
    assert "{broken json" in remaining and len(remaining) == 3


def test_not_a_quarantine(tmp_path):
    with pytest.raises(RestoreError):
        restore_quarantine(str(tmp_path))
    with pytest.raises(RestoreError):
        restore_quarantine(str(tmp_path / "missing"))


def test_cli_restore(tmp_path, capsys):
    q, _, e1, e2, _ = quarantined(tmp_path)
    assert main(["restore", str(q), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would restore" in out and "Dry run: would have restored 2 files" in out
    assert not e1.exists()
    assert main(["restore", str(q)]) == 0
    assert "Restored 2 files (34 B)" in capsys.readouterr().out
    assert e1.exists() and e2.exists()
    assert main(["restore", str(q)]) == 0
    assert "nothing to restore" in capsys.readouterr().out


def test_cli_restore_skips_exit_1(tmp_path, capsys):
    q, _, e1, _, _ = quarantined(tmp_path)
    write(e1, "something else")
    assert main(["restore", str(q)]) == 1
    out = capsys.readouterr().out
    assert "skipped" in out and "Restored 1 file (17 B), skipped 1" in out


def test_cli_restore_bad_folder(tmp_path, capsys):
    assert main(["restore", str(tmp_path)]) == 2
    assert "no manifest.jsonl" in capsys.readouterr().err
