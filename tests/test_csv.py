import csv
import io

import pytest

from dupefind.cli import main
from dupefind.report import CSV_COLUMNS


def make_tree(tmp_path):
    d = tmp_path / "t"
    d.mkdir()
    for name in ("a1", "a2"):
        (d / name).write_bytes(b"small")
    for name in ("b1", "b2", "b3"):
        (d / name).write_bytes(b"xy")
    (d / "uniq").write_bytes(b"unique!")
    return d


def test_csv_rows(tmp_path, capsys):
    d = make_tree(tmp_path)
    assert main(["scan", str(d), "--csv", "--sort", "count"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert [r["group"] for r in rows] == ["1", "1", "1", "2", "2"]
    assert [r["copies"] for r in rows] == ["3", "3", "3", "2", "2"]
    assert [r["wasted_bytes"] for r in rows[::3]] == ["4", "5"]
    assert {r["path"].rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for r in rows} == {
        "a1", "a2", "b1", "b2", "b3"}


def test_csv_ignores_limit_and_comma(tmp_path, capsys):
    d = tmp_path / "t"
    d.mkdir()
    for name in ("x, 1.txt", "x2.txt"):
        (d / name).write_bytes(b"same")
    assert main(["scan", str(d), "--csv", "--top", "1"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert len(rows) == 2
    assert any(r["path"].endswith("x, 1.txt") for r in rows)


def test_csv_no_duplicates_header_only(tmp_path, capsys):
    (tmp_path / "a").write_bytes(b"1")
    assert main(["scan", str(tmp_path), "--csv"]) == 0
    assert capsys.readouterr().out.strip() == ",".join(CSV_COLUMNS)


def test_csv_and_json_exclusive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(tmp_path), "--csv", "--json"])
    assert exc.value.code == 2
