import os
from datetime import datetime, timedelta, timezone

from dupefind.apply import apply_plan
from dupefind.cache import HashCache
from dupefind.longpath import io_path
from dupefind.purge import purge_quarantine
from dupefind.restore import restore_quarantine
from dupefind.scanner import find_duplicates


def _deep_dir(base, depth=7, width=45):
    d = str(base)
    for i in range(depth):
        d = os.path.join(d, f"{i}" + "d" * width)
    os.makedirs(io_path(d), exist_ok=True)
    return d


def _make_pair(tmp_path):
    deep = _deep_dir(tmp_path / "t")
    a, b = os.path.join(deep, "a.bin"), os.path.join(deep, "b.bin")
    for p in (a, b):
        with open(io_path(p), "wb") as fh:
            fh.write(b"same content" * 10)
    res = find_duplicates([str(tmp_path / "t")])
    plan = {
        "roots": [str(tmp_path / "t")],
        "groups": [{"size": 120, "sha256": res.groups[0].sha256, "keep": a, "remove": [b]}],
    }
    return a, b, plan


def test_restore_deep_paths(tmp_path):
    a, b, plan = _make_pair(tmp_path)
    q = str(tmp_path / "q")
    assert [x.status for x in apply_plan(plan, quarantine_dir=q).actions] == ["moved"]
    assert not os.path.exists(io_path(b))

    dry = restore_quarantine(q, dry_run=True)
    assert [x.status for x in dry.actions] == ["would restore"]
    assert not os.path.exists(io_path(b))

    rep = restore_quarantine(q)
    assert [x.status for x in rep.actions] == ["restored"], rep.actions
    assert os.path.exists(io_path(b))
    assert os.path.getsize(io_path(b)) == 120


def test_restore_refuses_overwrite_deep(tmp_path):
    a, b, plan = _make_pair(tmp_path)
    q = str(tmp_path / "q")
    apply_plan(plan, quarantine_dir=q)
    with open(io_path(b), "wb") as fh:
        fh.write(b"new")
    rep = restore_quarantine(q)
    assert [x.status for x in rep.actions] == ["skipped"]
    with open(io_path(b), "rb") as fh:
        assert fh.read() == b"new"


def test_purge_deep_paths(tmp_path):
    a, b, plan = _make_pair(tmp_path)
    q = str(tmp_path / "q")
    apply_plan(plan, quarantine_dir=q)
    later = datetime.now(timezone.utc) + timedelta(days=60)
    rep = purge_quarantine(q, timedelta(days=30), now=later)
    assert [x.status for x in rep.actions] == ["purged"], rep.actions
    assert os.path.exists(io_path(a))
    assert not os.path.exists(io_path(b))


def test_cache_file_in_deep_folder(tmp_path):
    deep = _deep_dir(tmp_path / "c")
    path = os.path.join(deep, "cache.json")
    cache = HashCache(path)
    cache.put("/some/file", 5, 1.0, None, "abc")
    cache.save()
    assert os.path.exists(io_path(path))
    again = HashCache(path)
    assert again.warning is None
    assert again.get("/some/file", 5, 1.0, None) == "abc"
