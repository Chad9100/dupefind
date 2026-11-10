import io

import pytest

from dupefind import cli
from dupefind.progress import ProgressLine, stream_is_tty
from dupefind.scanner import CHUNK_SIZE, find_duplicates


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TtyStream(io.StringIO):
    def isatty(self):
        return True


def test_progress_callback_reports_files_and_bytes(tmp_path):
    big = b"b" * (CHUNK_SIZE + 10)
    write(tmp_path / "a", big)
    write(tmp_path / "b", big)
    write(tmp_path / "c", "small")
    write(tmp_path / "d", "other")
    calls = []
    result = find_duplicates([str(tmp_path)], progress=lambda f, b: calls.append((f, b)))
    # One call per file found, then one per file hashed.
    assert [c[0] for c in calls[:4]] == [1, 2, 3, 4]
    assert all(c[1] == 0 for c in calls[:4])
    # Head pass hashes 64 KiB of a and b plus all of c and d; full pass hashes a and b.
    expected = 2 * CHUNK_SIZE + 5 + 5 + 2 * len(big)
    assert calls[-1] == (4, expected)
    assert result.bytes_hashed == expected
    byte_counts = [c[1] for c in calls]
    assert byte_counts == sorted(byte_counts)


def test_unique_sizes_are_never_hashed(tmp_path):
    write(tmp_path / "a", "1")
    write(tmp_path / "b", "22")
    result = find_duplicates([str(tmp_path)], progress=lambda f, b: None)
    assert result.bytes_hashed == 0


def test_progress_line_throttles_and_pads():
    out = io.StringIO()
    clock = FakeClock()
    line = ProgressLine(out, interval=1.0, clock=clock)
    line(1000, 2048)
    line(1001, 4096)  # within the interval: skipped
    assert out.getvalue() == "\rScanning: 1,000 files seen, 2.0 KiB hashed"
    clock.now = 1.5
    out.seek(0)
    out.truncate()
    line(5, 0)
    text = "\rScanning: 5 files seen, 0 B hashed"
    long_len = len("Scanning: 1,000 files seen, 2.0 KiB hashed")
    assert out.getvalue() == text + " " * (long_len - len(text) + 1)


def test_finish_erases_line_and_is_noop_when_nothing_drawn():
    out = io.StringIO()
    line = ProgressLine(out, clock=FakeClock())
    line.finish()
    assert out.getvalue() == ""
    line(3, 0)
    width = len(out.getvalue()) - 1
    line.finish()
    assert out.getvalue().endswith("\r" + " " * width + "\r")
    line.finish()
    assert out.getvalue().count("\r") == 3


def test_stream_is_tty():
    assert stream_is_tty(TtyStream()) is True
    assert stream_is_tty(io.StringIO()) is False

    class Closed:
        def isatty(self):
            raise ValueError("closed")

    assert stream_is_tty(Closed()) is False


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "data"
    write(d / "a.txt", "same bytes")
    write(d / "b.txt", "same bytes")
    return d


def test_cli_shows_progress_on_tty_stderr(tree, monkeypatch, capsys):
    err = TtyStream()
    monkeypatch.setattr(cli.sys, "stderr", err)
    assert cli.main(["scan", str(tree)]) == 0
    text = err.getvalue()
    assert "Scanning: 1 files seen" in text
    assert text.endswith("\r")  # line erased before the report
    assert "Scanning" not in capsys.readouterr().out


def test_cli_quiet_disables_progress(tree, monkeypatch):
    err = TtyStream()
    monkeypatch.setattr(cli.sys, "stderr", err)
    assert cli.main(["scan", "--quiet", str(tree)]) == 0
    assert err.getvalue() == ""


def test_cli_no_progress_when_stderr_not_tty(tree, tmp_path, monkeypatch, capsys):
    err = io.StringIO()
    monkeypatch.setattr(cli.sys, "stderr", err)
    plan = tmp_path / "plan.json"
    assert cli.main(["plan", str(tree), "-o", str(plan)]) == 0
    assert err.getvalue() == ""
    assert plan.exists()
