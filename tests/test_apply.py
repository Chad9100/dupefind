import json
import os

import pytest

from dupefind.apply import (
    DELETED,
    MANIFEST_NAME,
    MOVED,
    SKIPPED,
    WOULD_DELETE,
    WOULD_MOVE,
    apply_plan,
    quarantine_dest,
)
from dupefind.plan import build_plan
from dupefind.scanner import QUARANTINE_MARKER, find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def setup_dupes(tmp_path):
    data = tmp_path / "data"
    keep = write(data / "a.txt", "duplicate content")
    extra1 = write(data / "b.txt", "duplicate content")
    extra2 = write(data / "sub" / "c.txt", "duplicate content")
    os.utime(keep, (1000, 1000))
    os.utime(extra1, (2000, 2000))
    os.utime(extra2, (3000, 3000))
    plan = build_plan(find_duplicates([str(data)]), "oldest")
    return plan, keep, extra1, extra2


def test_quarantine_moves_extras(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    q = tmp_path / "q"
    report = apply_plan(plan, quarantine_dir=str(q))
    assert [a.status for a in report.actions] == [MOVED, MOVED]
    assert keep.exists() and not e1.exists() and not e2.exists()
    for a in report.actions:
        assert os.path.isfile(a.dest)
        assert a.dest.startswith(str(q))
    assert (q / QUARANTINE_MARKER).exists()
    assert report.reclaimed == 2 * len("duplicate content")


def test_quarantine_manifest(tmp_path):
    plan, keep, e1, _ = setup_dupes(tmp_path)
    q = tmp_path / "q"
    apply_plan(plan, quarantine_dir=str(q))
    records = [json.loads(line) for line in (q / MANIFEST_NAME).read_text("utf-8").splitlines()]
    assert len(records) == 2
    assert {r["original"] for r in records} == {str(e1), plan["groups"][0]["remove"][1]}
    assert all(r["kept"] == str(keep) for r in records)


def test_delete_mode(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    report = apply_plan(plan, delete=True)
    assert [a.status for a in report.actions] == [DELETED, DELETED]
    assert keep.exists() and not e1.exists() and not e2.exists()


def test_dry_run_changes_nothing(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    q = tmp_path / "q"
    report = apply_plan(plan, quarantine_dir=str(q), dry_run=True)
    assert [a.status for a in report.actions] == [WOULD_MOVE, WOULD_MOVE]
    assert e1.exists() and e2.exists() and not q.exists()
    report = apply_plan(plan, delete=True, dry_run=True)
    assert [a.status for a in report.actions] == [WOULD_DELETE, WOULD_DELETE]
    assert e1.exists() and e2.exists()


def test_changed_extra_is_skipped(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    e1.write_text("Duplicate content")  # same size, different bytes
    report = apply_plan(plan, delete=True)
    by_path = {a.path: a for a in report.actions}
    assert by_path[str(e1)].status == SKIPPED
    assert "content changed" in by_path[str(e1)].detail
    assert by_path[str(e2)].status == DELETED
    assert e1.exists()


def test_resized_extra_is_skipped(tmp_path):
    plan, keep, e1, _ = setup_dupes(tmp_path)
    e1.write_text("something longer than before")
    report = apply_plan(plan, delete=True)
    assert "size changed" in report.actions[0].detail
    assert e1.exists()


def test_missing_extra_is_skipped(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    e1.unlink()
    report = apply_plan(plan, delete=True)
    assert report.actions[0].status == SKIPPED
    assert "no longer exists" in report.actions[0].detail
    assert not e2.exists()


def test_missing_keep_protects_whole_group(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    keep.unlink()
    report = apply_plan(plan, delete=True)
    assert all(a.status == SKIPPED for a in report.actions)
    assert e1.exists() and e2.exists()


def test_changed_keep_protects_whole_group(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    keep.write_text("Duplicate content")
    report = apply_plan(plan, delete=True)
    assert all(a.status == SKIPPED for a in report.actions)
    assert e1.exists() and e2.exists()


def test_keep_listed_in_remove_is_never_touched(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    plan["groups"][0]["remove"].append(str(keep))
    report = apply_plan(plan, delete=True)
    assert keep.exists()
    assert report.actions[-1].status == SKIPPED


def test_path_listed_twice_handled_once(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    plan["groups"][0]["remove"].append(str(e1))
    report = apply_plan(plan, delete=True)
    assert [a.status for a in report.actions] == [DELETED, DELETED, SKIPPED]


def test_relative_path_refused(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    plan["groups"][0]["remove"] = ["relative.txt"]
    report = apply_plan(plan, delete=True)
    assert report.actions[0].status == SKIPPED
    assert "not absolute" in report.actions[0].detail


def test_hardlink_to_keep_is_skipped(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    alias = tmp_path / "data" / "alias.txt"
    try:
        os.link(keep, alias)
    except (OSError, NotImplementedError):
        pytest.skip("hard links not available")
    plan["groups"][0]["remove"] = [str(alias)]
    report = apply_plan(plan, delete=True)
    assert report.actions[0].status == SKIPPED
    assert "same physical file" in report.actions[0].detail
    assert alias.exists()


def test_symlink_extra_is_skipped(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    e1.unlink()
    try:
        os.symlink(keep, e1)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    report = apply_plan(plan, delete=True)
    assert report.actions[0].status == SKIPPED
    assert "symlink" in report.actions[0].detail
    assert keep.exists()


def test_name_collision_in_quarantine(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    q = tmp_path / "q"
    existing = quarantine_dest(str(q), str(e1), plan["roots"])
    os.makedirs(os.path.dirname(existing))
    with open(existing, "w") as fh:
        fh.write("older quarantined file")
    report = apply_plan(plan, quarantine_dir=str(q))
    assert report.actions[0].dest == existing + "~1"
    with open(existing) as fh:
        assert fh.read() == "older quarantined file"


def test_quarantine_dest_posix_and_windows_shapes(tmp_path):
    q = str(tmp_path / "q")
    src = str(tmp_path / "dir" / "f.txt")
    dest = quarantine_dest(q, src)
    assert dest.startswith(q)
    assert dest.endswith(os.path.join("dir", "f.txt"))
    assert ":" not in dest[len(q):]


def test_requires_quarantine_dir_or_delete():
    with pytest.raises(ValueError):
        apply_plan({"groups": []})


def test_rescan_after_quarantine_finds_nothing(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    q = tmp_path / "data" / "q"  # quarantine inside the scanned tree
    apply_plan(plan, quarantine_dir=str(q))
    assert find_duplicates([str(tmp_path / "data")]).groups == []


def test_quarantine_dest_relative_to_root(tmp_path):
    q = str(tmp_path / "q")
    root = str(tmp_path / "photos")
    dest = quarantine_dest(q, str(tmp_path / "photos" / "2024" / "a.jpg"), [root])
    assert dest == os.path.join(q, "photos", "2024", "a.jpg")


def test_quarantine_dest_prefers_outermost_root(tmp_path):
    q = str(tmp_path / "q")
    inner = str(tmp_path / "top" / "inner")
    outer = str(tmp_path / "top")
    dest = quarantine_dest(q, os.path.join(inner, "f"), [inner, outer])
    assert dest == os.path.join(q, "top", "inner", "f")


def test_quarantine_dest_file_root_and_outside(tmp_path):
    q = str(tmp_path / "q")
    f = str(tmp_path / "single.txt")
    assert quarantine_dest(q, f, [f]) == os.path.join(q, "single.txt")
    other = str(tmp_path / "elsewhere" / "x")
    assert quarantine_dest(q, other, [str(tmp_path / "photos")]) == quarantine_dest(q, other)


def test_quarantine_layout_uses_root_names(tmp_path):
    plan, keep, e1, e2 = setup_dupes(tmp_path)
    q = tmp_path / "q"
    apply_plan(plan, quarantine_dir=str(q))
    assert (q / "data" / "b.txt").is_file()
    assert (q / "data" / "sub" / "c.txt").is_file()
