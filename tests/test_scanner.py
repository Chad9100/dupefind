import hashlib
import os

import pytest

from dupefind import scanner
from dupefind.scanner import CHUNK_SIZE, QUARANTINE_MARKER, find_duplicates, hash_file


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def group_names(result):
    return sorted(sorted(os.path.basename(f.path) for f in g.files) for g in result.groups)


def test_hash_file_full_and_limited(tmp_path):
    p = write(tmp_path / "a.bin", b"x" * 1000)
    assert hash_file(str(p)) == hashlib.sha256(b"x" * 1000).hexdigest()
    assert hash_file(str(p), limit=10) == hashlib.sha256(b"x" * 10).hexdigest()


def test_finds_simple_duplicates(tmp_path):
    write(tmp_path / "a.txt", "hello")
    write(tmp_path / "sub" / "b.txt", "hello")
    write(tmp_path / "c.txt", "world")
    result = find_duplicates([str(tmp_path)])
    assert group_names(result) == [["a.txt", "b.txt"]]
    g = result.groups[0]
    assert g.size == 5
    assert g.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert g.wasted == 5
    assert result.files_scanned == 3
    assert result.bytes_scanned == 15


def test_same_size_different_content_not_grouped(tmp_path):
    write(tmp_path / "a", "aaaa")
    write(tmp_path / "b", "bbbb")
    assert find_duplicates([str(tmp_path)]).groups == []


def test_large_files_differing_after_first_chunk(tmp_path):
    base = b"z" * CHUNK_SIZE
    write(tmp_path / "a", base + b"1" * 100)
    write(tmp_path / "b", base + b"2" * 100)
    write(tmp_path / "c", base + b"1" * 100)
    result = find_duplicates([str(tmp_path)])
    assert group_names(result) == [["a", "c"]]
    assert result.groups[0].sha256 == hashlib.sha256(base + b"1" * 100).hexdigest()


def test_full_hash_skipped_for_small_files(tmp_path, monkeypatch):
    write(tmp_path / "a", "same")
    write(tmp_path / "b", "same")
    calls = []
    real = scanner.hash_file

    def spy(path, limit=None):
        calls.append(limit)
        return real(path, limit)

    monkeypatch.setattr(scanner, "hash_file", spy)
    result = find_duplicates([str(tmp_path)])
    assert len(result.groups) == 1
    assert calls == [CHUNK_SIZE, CHUNK_SIZE]


def test_unique_sizes_are_never_read(tmp_path, monkeypatch):
    write(tmp_path / "a", "1")
    write(tmp_path / "b", "22")
    write(tmp_path / "c", "333")

    def boom(path, limit=None):
        raise AssertionError("should not hash")

    monkeypatch.setattr(scanner, "hash_file", boom)
    assert find_duplicates([str(tmp_path)]).groups == []


def test_three_way_group_and_wasted(tmp_path):
    for name in ("a", "b", "c"):
        write(tmp_path / name, "x" * 10)
    result = find_duplicates([str(tmp_path)])
    assert len(result.groups[0].files) == 3
    assert result.wasted == 20
    assert result.redundant_copies == 2
    assert result.duplicate_files == 3


def test_empty_files_skipped_by_default(tmp_path):
    write(tmp_path / "a", b"")
    write(tmp_path / "b", b"")
    assert find_duplicates([str(tmp_path)]).groups == []
    assert len(find_duplicates([str(tmp_path)], min_size=0).groups) == 1


def test_min_size_filters(tmp_path):
    write(tmp_path / "s1", "ab")
    write(tmp_path / "s2", "ab")
    write(tmp_path / "b1", "x" * 100)
    write(tmp_path / "b2", "x" * 100)
    result = find_duplicates([str(tmp_path)], min_size=50)
    assert group_names(result) == [["b1", "b2"]]
    assert result.files_scanned == 2


def test_include_glob(tmp_path):
    write(tmp_path / "a.jpg", "pic")
    write(tmp_path / "b.jpg", "pic")
    write(tmp_path / "a.txt", "pic")
    result = find_duplicates([str(tmp_path)], include=["*.jpg"])
    assert group_names(result) == [["a.jpg", "b.jpg"]]


def test_exclude_glob_files(tmp_path):
    write(tmp_path / "a.tmp", "data")
    write(tmp_path / "b.tmp", "data")
    write(tmp_path / "c.dat", "data")
    write(tmp_path / "d.dat", "data")
    result = find_duplicates([str(tmp_path)], exclude=["*.tmp"])
    assert group_names(result) == [["c.dat", "d.dat"]]


def test_exclude_prunes_directories(tmp_path):
    write(tmp_path / "keep" / "a", "data")
    write(tmp_path / ".git" / "objects" / "b", "data")
    write(tmp_path / "keep" / "c", "data")
    result = find_duplicates([str(tmp_path)], exclude=[".git"])
    assert group_names(result) == [["a", "c"]]


def test_exclude_matches_relative_path(tmp_path):
    write(tmp_path / "build" / "out" / "a", "data")
    write(tmp_path / "src" / "a", "data")
    write(tmp_path / "src" / "b", "data")
    result = find_duplicates([str(tmp_path)], exclude=["build/*"])
    assert all("build" not in f.path for f in result.groups[0].files)
    assert len(result.groups[0].files) == 2


def test_multiple_roots_and_root_index(tmp_path):
    write(tmp_path / "one" / "a", "same")
    write(tmp_path / "two" / "a", "same")
    result = find_duplicates([str(tmp_path / "two"), str(tmp_path / "one")])
    files = result.groups[0].files
    assert [f.root_index for f in files] == [0, 1]
    assert files[0].path.startswith(str(tmp_path / "two"))


def test_overlapping_roots_count_files_once(tmp_path):
    write(tmp_path / "sub" / "a", "same")
    write(tmp_path / "b", "other")
    result = find_duplicates([str(tmp_path), str(tmp_path / "sub")])
    assert result.groups == []
    assert result.files_scanned == 2


def test_file_given_as_root(tmp_path):
    a = write(tmp_path / "a", "same")
    b = write(tmp_path / "x" / "b", "same")
    result = find_duplicates([str(a), str(b)])
    assert group_names(result) == [["a", "b"]]


def test_missing_root_reported(tmp_path):
    result = find_duplicates([str(tmp_path / "nope")])
    assert len(result.errors) == 1
    assert result.errors[0].path.endswith("nope")


def test_unreadable_file_is_reported_not_fatal(tmp_path, monkeypatch):
    write(tmp_path / "a", "same")
    write(tmp_path / "b", "same")
    write(tmp_path / "c", "same")
    real = scanner.hash_file

    def flaky(path, limit=None):
        if path.endswith("b"):
            raise PermissionError(13, "Permission denied", path)
        return real(path, limit)

    monkeypatch.setattr(scanner, "hash_file", flaky)
    result = find_duplicates([str(tmp_path)])
    assert group_names(result) == [["a", "c"]]
    assert [e.message for e in result.errors] == ["Permission denied"]


def test_unreadable_directory_is_reported(tmp_path, monkeypatch):
    write(tmp_path / "ok" / "a", "same")
    write(tmp_path / "ok" / "b", "same")
    write(tmp_path / "locked" / "c", "same")
    real = os.scandir

    def guarded(path):
        if str(path).endswith("locked"):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(scanner.os, "scandir", guarded)
    result = find_duplicates([str(tmp_path)])
    assert group_names(result) == [["a", "b"]]
    assert result.errors[0].path.endswith("locked")


def test_symlinks_are_skipped(tmp_path):
    target = write(tmp_path / "real", "same")
    write(tmp_path / "copy", "same")
    link = tmp_path / "link"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    result = find_duplicates([str(tmp_path)])
    assert group_names(result) == [["copy", "real"]]


def test_symlinked_directory_not_followed(tmp_path):
    write(tmp_path / "data" / "a", "same")
    write(tmp_path / "data" / "b", "same")
    try:
        os.symlink(tmp_path / "data", tmp_path / "loop", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    result = find_duplicates([str(tmp_path)])
    assert group_names(result) == [["a", "b"]]


def test_hardlinks_collapsed(tmp_path):
    a = write(tmp_path / "a", "same")
    try:
        os.link(a, tmp_path / "hard")
    except (OSError, NotImplementedError):
        pytest.skip("hard links not available")
    write(tmp_path / "copy", "same")
    result = find_duplicates([str(tmp_path)])
    assert len(result.groups) == 1
    assert len(result.groups[0].files) == 2
    assert result.hardlinks_skipped == 1


def test_quarantine_folder_is_skipped(tmp_path):
    write(tmp_path / "a", "same")
    q = tmp_path / "q"
    write(q / QUARANTINE_MARKER, "")
    write(q / "a", "same")
    assert find_duplicates([str(tmp_path)]).groups == []


def test_groups_sorted_by_wasted_space(tmp_path):
    write(tmp_path / "s1", "ab")
    write(tmp_path / "s2", "ab")
    write(tmp_path / "b1", "x" * 50)
    write(tmp_path / "b2", "x" * 50)
    result = find_duplicates([str(tmp_path)])
    assert [g.size for g in result.groups] == [50, 2]


def test_unicode_file_names(tmp_path):
    write(tmp_path / "café.txt", "same")
    write(tmp_path / "日本.txt", "same")
    assert len(find_duplicates([str(tmp_path)]).groups) == 1
