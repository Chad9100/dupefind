import os

from dupefind.cli import main
from dupefind.report import folder_summary
from dupefind.scanner import DupGroup, FileInfo, ScanResult


def make_result():
    def g(size, paths):
        return DupGroup(size, "00" * 32, [FileInfo(p, size, 1.0, 0) for p in paths])

    groups = [
        g(100, [os.path.join("a", "x"), os.path.join("b", "x")]),
        g(10, [os.path.join("a", "y"), os.path.join("a", "z"), os.path.join("b", "y")]),
    ]
    return ScanResult(roots=["."], groups=groups)


def test_folder_summary_counts_every_copy():
    rows = folder_summary(make_result())
    assert rows == [("a", 3, 120), ("b", 2, 110)]


def test_folder_summary_empty():
    assert folder_summary(ScanResult(roots=["."])) == []


def test_cli_by_folder(tmp_path, capsys):
    for d in ("one", "two"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "f.bin").write_bytes(b"z" * 2048)
    (tmp_path / "two" / "u.bin").write_bytes(b"unique-content")
    assert main(["scan", str(tmp_path), "--by-folder", "-q"]) == 0
    out = capsys.readouterr().out
    assert "Folders holding duplicate data" in out
    assert str(tmp_path / "one") in out and str(tmp_path / "two") in out
    assert "2.0 KiB" in out and "1 file" in out


def test_cli_by_folder_limit(tmp_path, capsys):
    for d in ("one", "two"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "f.bin").write_bytes(b"z" * 2048)
    main(["scan", str(tmp_path), "--by-folder", "--top", "1", "-q"])
    out = capsys.readouterr().out
    assert "1 more folder" in out


def test_cli_by_folder_none(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("only")
    main(["scan", str(tmp_path), "--by-folder", "-q"])
    assert "No duplicates found." in capsys.readouterr().out
