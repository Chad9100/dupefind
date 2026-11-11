import json

import dupefind.apply as apply_mod
from dupefind.apply import apply_plan, device_of
from dupefind.cli import main
from dupefind.plan import build_plan
from dupefind.scanner import find_duplicates


def make(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.txt").write_bytes(b"duplicate content")
    (data / "b.txt").write_bytes(b"duplicate content")
    return build_plan(find_duplicates([str(data)]), "shortest-path")


def test_same_device_has_no_warning(tmp_path):
    plan = make(tmp_path)
    report = apply_plan(plan, quarantine_dir=str(tmp_path / "q"), dry_run=True)
    assert report.cross_device == 0


def test_device_of_walks_up_to_existing_parent(tmp_path):
    assert device_of(str(tmp_path / "not" / "there" / "yet")) == device_of(str(tmp_path))


def test_cross_device_counted(tmp_path, monkeypatch):
    plan = make(tmp_path)
    monkeypatch.setattr(apply_mod, "device_of", lambda p: -1)
    report = apply_plan(plan, quarantine_dir=str(tmp_path / "q"), dry_run=True)
    assert report.cross_device == 1
    assert report.cross_device_bytes == len("duplicate content")


def test_delete_and_link_never_warn(tmp_path, monkeypatch):
    plan = make(tmp_path)
    monkeypatch.setattr(apply_mod, "device_of", lambda p: -1)
    assert apply_plan(plan, delete=True, dry_run=True).cross_device == 0
    assert apply_plan(plan, link="hardlink", dry_run=True).cross_device == 0


def test_cli_warns_on_stderr(tmp_path, monkeypatch, capsys):
    plan = make(tmp_path)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(apply_mod, "device_of", lambda p: -1)
    code = main(["apply", str(plan_file), "--dry-run", "--quarantine", str(tmp_path / "q")])
    out = capsys.readouterr()
    assert code == 0
    assert "different drive than the quarantine folder" in out.err
    assert "different drive" not in out.out
    assert "would move" in out.out
