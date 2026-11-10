import json
import os

import pytest

from dupefind import __version__
from dupefind.cli import main


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "data"
    write(d / "a.txt", "same bytes")
    write(d / "b" / "a.txt", "same bytes")
    write(d / "c.log", "same bytes")
    write(d / "unique.txt", "nothing like it")
    return d


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_scan_human(tree, capsys):
    assert main(["scan", str(tree)]) == 0
    out = capsys.readouterr().out
    assert "Found 1 duplicate group: 3 files, 2 redundant copies" in out
    assert "Wasted space: 20 B" in out


def test_scan_json(tree, capsys):
    assert main(["scan", str(tree), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["duplicate_groups"] == 1
    assert data["wasted_bytes"] == 20
    assert len(data["groups"][0]["files"]) == 3


def test_scan_filters(tree, capsys):
    main(["scan", str(tree), "--include", "*.txt", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data["groups"][0]["files"]) == 2
    main(["scan", str(tree), "--exclude", "b", "--min-size", "1K", "--json"])
    assert json.loads(capsys.readouterr().out)["duplicate_groups"] == 0


def test_scan_bad_min_size(tree, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(tree), "--min-size", "lots"])
    assert exc.value.code == 2
    assert "invalid size" in capsys.readouterr().err


def test_scan_never_modifies(tree):
    before = sorted(p for p in tree.rglob("*"))
    main(["scan", str(tree)])
    assert sorted(p for p in tree.rglob("*")) == before


def test_plan_then_apply_quarantine(tree, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    assert main(["plan", str(tree), "-o", str(plan), "--keep", "shortest-path"]) == 0
    out = capsys.readouterr().out
    assert "2 files to remove" in out
    data = json.loads(plan.read_text("utf-8"))
    assert os.path.basename(data["groups"][0]["keep"]) in ("a.txt", "c.log")

    assert main(["apply", str(plan)]) == 0
    out = capsys.readouterr().out
    assert "Moved 2 files" in out
    q = tmp_path / "dupefind-quarantine"
    assert q.is_dir()
    remaining = [p for p in tree.rglob("*") if p.is_file()]
    assert len(remaining) == 2  # one kept copy + unique.txt


def test_plan_first_in(tmp_path, capsys):
    write(tmp_path / "master" / "x", "content")
    write(tmp_path / "backup" / "x", "content")
    plan = tmp_path / "plan.json"
    main(["plan", str(tmp_path / "master"), str(tmp_path / "backup"),
          "--keep", "first-in", "-o", str(plan)])
    data = json.loads(plan.read_text("utf-8"))
    assert data["groups"][0]["keep"] == str(tmp_path / "master" / "x")


def test_plan_remove_and_keep_pattern(tmp_path):
    write(tmp_path / "Downloads" / "x", "content")
    write(tmp_path / "Archive" / "x", "content")
    for flag, glob in (("--remove-pattern", "*/Downloads/*"), ("--keep-pattern", "*/Archive/*")):
        plan = tmp_path / "plan.json"
        plan.unlink(missing_ok=True)
        # first-in would keep Downloads (listed first); the pattern overrides it
        assert main(["plan", str(tmp_path / "Downloads"), str(tmp_path / "Archive"),
                     "--keep", "first-in", flag, glob, "-o", str(plan)]) == 0
        data = json.loads(plan.read_text("utf-8"))
        assert data["groups"][0]["keep"] == str(tmp_path / "Archive" / "x")
        assert data["groups"][0]["remove"] == [str(tmp_path / "Downloads" / "x")]


def test_plan_refuses_overwrite(tree, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text("keep me")
    assert main(["plan", str(tree), "-o", str(plan)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert plan.read_text() == "keep me"
    assert main(["plan", str(tree), "-o", str(plan), "--force"]) == 0


def test_plan_no_duplicates_writes_nothing(tmp_path, capsys):
    write(tmp_path / "d" / "only", "one")
    plan = tmp_path / "plan.json"
    assert main(["plan", str(tmp_path / "d"), "-o", str(plan)]) == 0
    assert not plan.exists()
    assert "no plan written" in capsys.readouterr().out


def test_apply_dry_run_then_delete(tree, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    main(["plan", str(tree), "-o", str(plan)])
    capsys.readouterr()
    assert main(["apply", str(plan), "--delete", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would delete" in out and "Dry run: would have deleted 2 files" in out
    assert len([p for p in tree.rglob("*") if p.is_file()]) == 4
    assert main(["apply", str(plan), "--delete"]) == 0
    assert len([p for p in tree.rglob("*") if p.is_file()]) == 2
    assert not (tmp_path / "dupefind-quarantine").exists()


def test_apply_custom_quarantine(tree, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    main(["plan", str(tree), "-o", str(plan)])
    q = tmp_path / "elsewhere"
    assert main(["apply", str(plan), "--quarantine", str(q)]) == 0
    assert (q / "manifest.jsonl").exists()


def test_apply_reports_skips_with_exit_1(tree, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    main(["plan", str(tree), "-o", str(plan)])
    data = json.loads(plan.read_text("utf-8"))
    victim = data["groups"][0]["remove"][0]
    with open(victim, "w") as fh:
        fh.write("SAME BYTES")
    capsys.readouterr()
    assert main(["apply", str(plan), "--delete"]) == 1
    out = capsys.readouterr().out
    assert "skipped" in out and "content changed" in out
    assert os.path.exists(victim)


def test_apply_bad_plan(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}')
    assert main(["apply", str(bad)]) == 2
    assert "not a dupefind plan" in capsys.readouterr().err


def test_python_dash_m(tree):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "dupefind", "scan", str(tree), "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["duplicate_groups"] == 1
