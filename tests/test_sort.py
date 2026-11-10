import json

import pytest

from dupefind.cli import main
from dupefind.report import sort_groups
from dupefind.scanner import DupGroup, FileInfo


def group(size, paths):
    return DupGroup(size, "00" * 32, [FileInfo(p, size, 1.0, 0) for p in paths])


GROUPS = [
    group(10, ["/m/a", "/m/b", "/m/c", "/m/d"]),  # wasted 30, count 4
    group(100, ["/z/a", "/z/b"]),  # wasted 100, count 2
    group(50, ["/b/a", "/b/b", "/b/c"]),  # wasted 100, count 3
]


@pytest.mark.parametrize(
    "key,expected",
    [
        ("wasted", ["/b/a", "/z/a", "/m/a"]),
        ("size", ["/z/a", "/b/a", "/m/a"]),
        ("count", ["/m/a", "/b/a", "/z/a"]),
        ("path", ["/b/a", "/m/a", "/z/a"]),
    ],
)
def test_sort_groups(key, expected):
    assert [g.files[0].path for g in sort_groups(GROUPS, key)] == expected


def test_sort_groups_unknown_key():
    with pytest.raises(ValueError):
        sort_groups(GROUPS, "bogus")


def make_tree(tmp_path):
    d = tmp_path / "t"
    d.mkdir()
    for name in ("a1", "a2"):  # two copies, 5 bytes
        (d / name).write_bytes(b"small")
    for name in ("b1", "b2", "b3"):  # three copies, 2 bytes
        (d / name).write_bytes(b"xy")
    return d


def test_scan_sort_count_json(tmp_path, capsys):
    d = make_tree(tmp_path)
    assert main(["scan", str(d), "--json", "--sort", "count"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [len(g["files"]) for g in data["groups"]] == [3, 2]
    assert main(["scan", str(d), "--json", "--sort", "size"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [g["size"] for g in data["groups"]] == [5, 2]


def test_scan_top_alias(tmp_path, capsys):
    d = make_tree(tmp_path)
    assert main(["scan", str(d), "--top", "1", "--sort", "path"]) == 0
    out = capsys.readouterr().out
    assert "[1]" in out and "[2]" not in out
    assert "1 more group" in out


def test_scan_bad_sort(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(tmp_path), "--sort", "bogus"])
    assert exc.value.code == 2
