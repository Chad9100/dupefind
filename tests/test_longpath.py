import os

from dupefind.apply import apply_plan
from dupefind.longpath import display_path, io_path, to_extended
from dupefind.scanner import find_duplicates


def test_short_paths_untouched():
    assert to_extended("C:\\data\\a.txt") == "C:\\data\\a.txt"


def test_long_drive_path_gets_prefix():
    p = "C:\\" + "\\".join(["d" * 50] * 6) + "\\f.txt"
    assert to_extended(p) == "\\\\?\\" + p


def test_long_unc_path_gets_unc_prefix():
    p = "\\\\srv\\share\\" + "\\".join(["d" * 50] * 6)
    assert to_extended(p) == "\\\\?\\UNC\\srv\\share\\" + "\\".join(["d" * 50] * 6)


def test_already_prefixed_not_doubled():
    p = "\\\\?\\C:\\" + "x" * 300
    assert to_extended(p) == p


def test_display_path_round_trip():
    assert display_path("\\\\?\\C:\\a\\b") == "C:\\a\\b"
    assert display_path("\\\\?\\UNC\\srv\\share\\a") == "\\\\srv\\share\\a"
    assert display_path("/plain/path") == "/plain/path"


def test_io_path_noop_off_windows():
    if os.name != "nt":
        assert io_path("/x/" + "y" * 400) == "/x/" + "y" * 400


def _deep_dir(base, depth=7, width=45):
    d = str(base)
    for i in range(depth):
        d = os.path.join(d, f"{i}" + "d" * width)
    os.makedirs(io_path(d), exist_ok=True)
    return d


def test_scan_and_apply_in_deep_tree(tmp_path):
    deep = _deep_dir(tmp_path / "t")
    if os.name == "nt":
        assert len(deep) > 260
    a = os.path.join(deep, "a.bin")
    b = os.path.join(deep, "b.bin")
    for p in (a, b):
        with open(io_path(p), "wb") as fh:
            fh.write(b"same content" * 10)

    res = find_duplicates([str(tmp_path / "t")])
    assert not res.errors
    assert len(res.groups) == 1
    paths = sorted(f.path for f in res.groups[0].files)
    assert paths == sorted([a, b])
    assert not any(p.startswith("\\\\?\\") for p in paths)

    plan = {
        "roots": [str(tmp_path / "t")],
        "groups": [{"size": 120, "sha256": res.groups[0].sha256, "keep": a, "remove": [b]}],
    }
    report = apply_plan(plan, quarantine_dir=str(tmp_path / "q"))
    assert [x.status for x in report.actions] == ["moved"]
    assert os.path.exists(io_path(a))
    assert not os.path.exists(io_path(b))
