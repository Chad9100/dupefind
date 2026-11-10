import json
import threading

import pytest

from dupefind import scanner
from dupefind.cli import main
from dupefind.scanner import CHUNK_SIZE, find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def make_tree(root):
    big = b"B" * (CHUNK_SIZE * 2)
    same_head = b"B" * (CHUNK_SIZE * 2 - 1) + b"x"  # differs only in the last byte
    for i in range(3):
        write(root / f"dir{i}" / "big.bin", big)
    write(root / "near.bin", same_head)
    for i in range(10):
        write(root / f"pair{i}" / "a.txt", f"pair {i}")
        write(root / f"pair{i}" / "b.txt", f"pair {i}")
    write(root / "lonely.txt", "only one of me")
    return root


def summary(result):
    return (
        [(g.size, g.sha256, [f.path for f in g.files]) for g in result.groups],
        result.files_scanned,
        result.bytes_hashed,
        sorted(result.errors, key=lambda e: e.path),
    )


@pytest.mark.parametrize("jobs", [2, 4, 16])
def test_threads_give_same_result_as_one(tmp_path, jobs):
    root = make_tree(tmp_path / "data")
    one = find_duplicates([str(root)])
    many = find_duplicates([str(root)], jobs=jobs)
    assert summary(many) == summary(one)
    assert len(one.groups) == 11
    assert all(not g.files[0].path.endswith("near.bin") for g in one.groups)


def test_hashing_really_runs_in_parallel(tmp_path, monkeypatch):
    write(tmp_path / "a", "same")
    write(tmp_path / "b", "same")
    # Each hash waits until the other one has started: this only finishes when
    # two threads hash at the same time (with one thread the barrier breaks).
    barrier = threading.Barrier(2, timeout=10)
    real = scanner.hash_file

    def hash_together(path, limit=None):
        barrier.wait()
        return real(path, limit)

    monkeypatch.setattr(scanner, "hash_file", hash_together)
    result = find_duplicates([str(tmp_path)], jobs=2)
    assert len(result.groups) == 1


def test_errors_in_worker_threads_are_reported(tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        write(tmp_path / name, "same")
    real = scanner.hash_file

    def flaky(path, limit=None):
        if path.endswith("b"):
            raise PermissionError(13, "Permission denied")
        return real(path, limit)

    monkeypatch.setattr(scanner, "hash_file", flaky)
    result = find_duplicates([str(tmp_path)], jobs=3)
    assert [len(g.files) for g in result.groups] == [2]
    assert [(e.path.endswith("b"), e.message) for e in result.errors] == [
        (True, "Permission denied")
    ]


def test_progress_is_called_from_the_calling_thread(tmp_path):
    make_tree(tmp_path)
    threads = set()
    find_duplicates([str(tmp_path)], jobs=4, progress=lambda f, b: threads.add(threading.get_ident()))
    assert threads == {threading.get_ident()}


def test_jobs_below_one_rejected(tmp_path):
    with pytest.raises(ValueError):
        find_duplicates([str(tmp_path)], jobs=0)


def test_cli_jobs(tmp_path, capsys):
    root = make_tree(tmp_path / "data")
    assert main(["scan", str(root), "--json", "--jobs", "4"]) == 0
    many = json.loads(capsys.readouterr().out)
    assert main(["scan", str(root), "--json"]) == 0
    one = json.loads(capsys.readouterr().out)
    assert many == one
    assert len(many["groups"]) == 11


def test_cli_plan_accepts_jobs(tmp_path, capsys):
    root = make_tree(tmp_path / "data")
    out = tmp_path / "plan.json"
    assert main(["plan", str(root), "-j", "2", "-o", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["groups"] == 11


@pytest.mark.parametrize("value", ["0", "-2", "many"])
def test_cli_bad_jobs_is_usage_error(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(tmp_path), "--jobs", value])
    assert exc.value.code == 2
    assert "--jobs" in capsys.readouterr().err
