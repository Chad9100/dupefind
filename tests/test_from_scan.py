import json

import pytest

from dupefind.cli import main


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode())
    return path


@pytest.fixture
def two_roots(tmp_path):
    master = tmp_path / "master"
    backup = tmp_path / "backup"
    write(master / "a.txt", "same bytes")
    write(backup / "a.txt", "same bytes")
    write(backup / "b.txt", "other")
    return master, backup


def saved_scan(tmp_path, capsys, *paths):
    assert main(["scan", *map(str, paths), "--json"]) == 0
    f = tmp_path / "scan.json"
    f.write_text(capsys.readouterr().out, encoding="utf-8")
    return f


def test_from_scan_matches_live_plan(tmp_path, two_roots, capsys):
    master, backup = two_roots
    scan = saved_scan(tmp_path, capsys, backup, master)
    live = tmp_path / "live.json"
    saved = tmp_path / "saved.json"
    assert main(["plan", str(backup), str(master), "--keep", "first-in", "-o", str(live)]) == 0
    assert main(["plan", "--from-scan", str(scan), "--keep", "first-in", "-o", str(saved)]) == 0
    a, b = json.loads(live.read_text("utf-8")), json.loads(saved.read_text("utf-8"))
    assert a["groups"] == b["groups"]
    assert b["groups"][0]["keep"].endswith("a.txt")
    assert "backup" in b["groups"][0]["keep"]  # first-in honours the saved root order


def test_from_scan_plan_is_applicable(tmp_path, two_roots, capsys):
    master, backup = two_roots
    scan = saved_scan(tmp_path, capsys, master, backup)
    plan = tmp_path / "p.json"
    assert main(["plan", "--from-scan", str(scan), "-o", str(plan)]) == 0
    assert main(["apply", str(plan), "--dry-run"]) == 0


def test_from_scan_rejects_paths(tmp_path, two_roots, capsys):
    scan = saved_scan(tmp_path, capsys, two_roots[0], two_roots[1])
    assert main(["plan", str(two_roots[0]), "--from-scan", str(scan)]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_from_scan_bad_files(tmp_path, capsys):
    assert main(["plan", "--from-scan", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert main(["plan", "--from-scan", str(bad)]) == 2
    other = tmp_path / "other.json"
    other.write_text('{"hello": 1}', encoding="utf-8")
    assert main(["plan", "--from-scan", str(other), "-o", str(tmp_path / "x.json")]) == 2
    assert "scan --json" in capsys.readouterr().err


def test_plan_and_scan_need_paths(capsys):
    assert main(["plan"]) == 2
    assert main(["scan"]) == 2
