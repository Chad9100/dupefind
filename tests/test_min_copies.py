import json

import pytest

from dupefind.cli import main
from dupefind.scanner import find_duplicates


def make_tree(base):
    base.mkdir()
    for name in ("a1.txt", "a2.txt", "a3.txt"):
        (base / name).write_bytes(b"A" * 300)
    for name in ("b1.txt", "b2.txt"):
        (base / name).write_bytes(b"B" * 500)
    return base


def test_default_reports_pairs_and_triples(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))])
    assert sorted(len(g.files) for g in result.groups) == [2, 3]


def test_min_copies_three_drops_pairs(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))], min_copies=3)
    assert [len(g.files) for g in result.groups] == [3]
    assert result.wasted == 600
    assert result.files_scanned == 5


def test_min_copies_above_all_groups(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))], min_copies=4)
    assert result.groups == []


def test_min_copies_below_two_raises(tmp_path):
    with pytest.raises(ValueError):
        find_duplicates([str(make_tree(tmp_path / "d"))], min_copies=1)


def test_cli_scan_json(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    assert main(["scan", str(root), "--json", "--min-copies", "3"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["groups"]) == 1
    assert len(data["groups"][0]["files"]) == 3


def test_cli_plan_honours_min_copies(tmp_path):
    root = make_tree(tmp_path / "d")
    out = tmp_path / "plan.json"
    assert main(["plan", str(root), "--min-copies", "3", "-o", str(out)]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["summary"]["groups"] == 1
    assert plan["summary"]["files_to_remove"] == 2


def test_cli_rejects_bad_value(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(root), "--min-copies", "1"])
    assert exc.value.code == 2
    assert "at least 2" in capsys.readouterr().err
