import json

import pytest

from dupefind.cli import main
from dupefind.scanner import find_duplicates


def make_tree(base):
    base.mkdir()
    for name in ("small1.bin", "small2.bin"):
        (base / name).write_bytes(b"s" * 100)
    for name in ("big1.bin", "big2.bin"):
        (base / name).write_bytes(b"B" * 5000)
    return base


def names(result):
    return sorted(f.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                  for g in result.groups for f in g.files)


def test_no_limit_by_default(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))])
    assert result.files_scanned == 4
    assert len(result.groups) == 2


def test_max_size_skips_big_files(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))], max_size=1000)
    assert result.files_scanned == 2
    assert names(result) == ["small1.bin", "small2.bin"]


def test_max_size_is_inclusive(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))], max_size=5000)
    assert result.files_scanned == 4


def test_min_and_max_form_a_band(tmp_path):
    result = find_duplicates([str(make_tree(tmp_path / "d"))], min_size=1000, max_size=9000)
    assert names(result) == ["big1.bin", "big2.bin"]


def test_max_below_min_raises(tmp_path):
    with pytest.raises(ValueError):
        find_duplicates([str(make_tree(tmp_path / "d"))], min_size=500, max_size=100)


def test_cli_scan(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    assert main(["scan", str(root), "--json", "--max-size", "1K"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["groups"]) == 1
    assert len(data["groups"][0]["files"]) == 2


def test_cli_rejects_inverted_range(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    assert main(["scan", str(root), "--min-size", "1K", "--max-size", "10"]) == 2
    assert "smaller than --min-size" in capsys.readouterr().err
    assert main(["plan", str(root), "--min-size", "1K", "--max-size", "10"]) == 2


def test_cli_plan_honours_max_size(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    out = tmp_path / "plan.json"
    assert main(["plan", str(root), "--max-size", "1K", "-o", str(out)]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["summary"]["groups"] == 1
