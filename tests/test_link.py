import json
import os

import pytest

from dupefind.apply import LINKED, SKIPPED, WOULD_LINK, apply_plan
from dupefind.cli import main
from dupefind.plan import build_plan
from dupefind.scanner import find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode())
    return path


def make(tmp_path):
    data = tmp_path / "data"
    keep = write(data / "a.txt", "duplicate content")
    extra = write(data / "sub" / "b.txt", "duplicate content")
    os.utime(keep, (1000, 1000))
    os.utime(extra, (2000, 2000))
    plan = build_plan(find_duplicates([str(data)]), "oldest")
    return plan, keep, extra


def test_hardlink_replaces_extra(tmp_path):
    plan, keep, extra = make(tmp_path)
    report = apply_plan(plan, link="hardlink")
    assert [a.status for a in report.actions] == [LINKED]
    assert os.path.samefile(keep, extra)
    assert extra.read_text() == "duplicate content"
    assert report.reclaimed == len("duplicate content")
    assert not [p for p in os.listdir(extra.parent) if "dupefind-link" in p]


def test_hardlink_dry_run_changes_nothing(tmp_path):
    plan, keep, extra = make(tmp_path)
    report = apply_plan(plan, link="hardlink", dry_run=True)
    assert [a.status for a in report.actions] == [WOULD_LINK]
    assert not os.path.samefile(keep, extra)


def test_symlink_replaces_extra(tmp_path):
    plan, keep, extra = make(tmp_path)
    report = apply_plan(plan, link="symlink")
    if report.actions[0].status == SKIPPED:
        pytest.skip("symlinks unavailable: " + report.actions[0].detail)
    assert os.path.islink(extra)
    assert extra.read_text() == "duplicate content"


def test_failed_link_leaves_file_alone(tmp_path, monkeypatch):
    plan, keep, extra = make(tmp_path)

    def boom(*a, **k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", boom)
    report = apply_plan(plan, link="hardlink")
    assert report.actions[0].status == SKIPPED
    assert "cannot create hardlink" in report.actions[0].detail
    assert extra.read_text() == "duplicate content"
    assert not os.path.samefile(keep, extra)


def test_different_volume_is_skipped(tmp_path, monkeypatch):
    plan, keep, extra = make(tmp_path)
    real = os.lstat

    class Other:
        def __init__(self, st):
            self._st = st
            self.st_dev = st.st_dev + 1

        def __getattr__(self, name):
            return getattr(self._st, name)

    def fake(path, *a, **k):
        st = real(path, *a, **k)
        if os.path.abspath(str(path)) == str(extra):
            return Other(st)
        return st

    monkeypatch.setattr(os, "lstat", fake)
    report = apply_plan(plan, link="hardlink")
    assert report.actions[0].status == SKIPPED
    assert "different volume" in report.actions[0].detail


def test_link_and_delete_rejected(tmp_path):
    plan, _, _ = make(tmp_path)
    with pytest.raises(ValueError):
        apply_plan(plan, link="hardlink", delete=True)
    with pytest.raises(ValueError):
        apply_plan(plan, link="bogus")


def test_cli_hardlink(tmp_path, capsys):
    plan, keep, extra = make(tmp_path)
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan), encoding="utf-8")
    assert main(["apply", str(planfile), "--hardlink", "--dry-run"]) == 0
    assert "would link" in capsys.readouterr().out
    assert main(["apply", str(planfile), "--hardlink"]) == 0
    out = capsys.readouterr().out
    assert "linked" in out and "Linked 1 file" in out
    assert os.path.samefile(keep, extra)
    assert not (tmp_path / "dupefind-quarantine").exists()


def test_cli_rejects_conflicting_flags(tmp_path, capsys):
    plan, _, _ = make(tmp_path)
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan), encoding="utf-8")
    assert main(["apply", str(planfile), "--hardlink", "--symlink"]) == 2
    assert main(["apply", str(planfile), "--hardlink", "--delete"]) == 2
