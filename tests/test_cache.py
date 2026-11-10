import json
import os

from dupefind import scanner
from dupefind.cache import HashCache
from dupefind.cli import main
from dupefind.scanner import CHUNK_SIZE, find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_tree(root):
    big = b"B" * (CHUNK_SIZE * 2)
    write(root / "a" / "big.bin", big)
    write(root / "b" / "big.bin", big)
    write(root / "a" / "small.txt", b"small")
    write(root / "b" / "small.txt", b"small")
    write(root / "solo.txt", b"unique content")


def groups(result):
    return [(g.size, g.sha256, [f.path for f in g.files]) for g in result.groups]


def test_second_scan_uses_cache(tmp_path):
    tree = tmp_path / "tree"
    make_tree(tree)
    cache_file = tmp_path / "cache" / "hashes.json"

    first = find_duplicates([str(tree)], cache=HashCache(str(cache_file)))
    assert first.cache_hits == 0 and first.bytes_hashed > 0
    cache = HashCache(str(cache_file))
    cache.dirty = True
    # Nothing was saved yet by find_duplicates itself.
    assert not cache_file.exists()

    c1 = HashCache(str(cache_file))
    r1 = find_duplicates([str(tree)], cache=c1)
    c1.save()
    assert cache_file.exists()

    c2 = HashCache(str(cache_file))
    r2 = find_duplicates([str(tree)], cache=c2)
    assert r2.bytes_hashed == 0
    assert r2.cache_hits > 0
    assert groups(r2) == groups(r1) == groups(first)


def test_changed_file_is_rehashed(tmp_path):
    tree = tmp_path / "tree"
    make_tree(tree)
    cache_file = tmp_path / "hashes.json"
    c1 = HashCache(str(cache_file))
    find_duplicates([str(tree)], cache=c1)
    c1.save()

    target = tree / "b" / "big.bin"
    st = target.stat()
    target.write_bytes(b"C" * (CHUNK_SIZE * 2))  # same size, new content
    os.utime(target, (st.st_atime, st.st_mtime + 10))

    r = find_duplicates([str(tree)], cache=HashCache(str(cache_file)))
    names = [os.path.basename(f.path) for g in r.groups for f in g.files]
    assert names == ["small.txt", "small.txt"]  # big.bin no longer duplicates
    assert r.bytes_hashed > 0


def test_corrupt_cache_is_ignored(tmp_path):
    cache_file = tmp_path / "hashes.json"
    cache_file.write_text("{not json", encoding="utf-8")
    cache = HashCache(str(cache_file))
    assert cache.warning
    cache_file.write_text(json.dumps({"version": 99, "entries": {}}), encoding="utf-8")
    assert HashCache(str(cache_file)).warning


def test_cached_result_not_trusted_when_size_differs(tmp_path):
    f = write(tmp_path / "x.bin", b"abc")
    st = f.stat()
    cache = HashCache(str(tmp_path / "c.json"))
    cache.put(str(f), st.st_size, st.st_mtime, None, "deadbeef")
    assert cache.get(str(f), st.st_size, st.st_mtime, None) == "deadbeef"
    assert cache.get(str(f), st.st_size + 1, st.st_mtime, None) is None
    assert cache.get(str(f), st.st_size, st.st_mtime + 1, None) is None
    assert cache.get(str(f), st.st_size, st.st_mtime, 10) is None


def test_cli_cache_option(tmp_path, capsys):
    tree = tmp_path / "tree"
    make_tree(tree)
    cache_file = tmp_path / "cli-cache.json"
    args = ["scan", str(tree), "--json", "-q", "--cache", str(cache_file)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert cache_file.exists()

    calls = []
    real = scanner.hash_file
    scanner.hash_file = lambda *a, **k: calls.append(a) or real(*a, **k)
    try:
        assert main(args) == 0
    finally:
        scanner.hash_file = real
    second = json.loads(capsys.readouterr().out)
    assert calls == []
    assert second["groups"] == first["groups"]
