import json

import pytest

from dupefind.report import format_size, parse_size, render_report, result_to_dict
from dupefind.scanner import DupGroup, FileInfo, ScanError, ScanResult


def sample_result():
    files = [FileInfo("/x/a", 2048, 1.0, 0), FileInfo("/x/b", 2048, 2.0, 0)]
    return ScanResult(
        roots=["/x"],
        groups=[DupGroup(2048, "ab" * 32, files)],
        errors=[ScanError("/x/locked", "Permission denied")],
        files_scanned=3,
        bytes_scanned=4106,
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0", 0),
        ("100", 100),
        ("4K", 4096),
        ("4kb", 4096),
        ("1.5M", 1572864),
        ("2GiB", 2 * 1024**3),
        (" 10 B ", 10),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "-1", "5X", "1.2.3"])
def test_parse_size_rejects(text):
    with pytest.raises(ValueError):
        parse_size(text)


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KiB"
    assert format_size(5 * 1024**2) == "5.0 MiB"
    assert format_size(3 * 1024**4) == "3.0 TiB"


def test_render_report_contents():
    text = render_report(sample_result())
    assert "Scanned 3 files (4.0 KiB) in 1 root" in text
    assert "Found 1 duplicate group: 2 files, 1 redundant copy" in text
    assert "Wasted space: 2.0 KiB" in text
    assert "    /x/a" in text and "    /x/b" in text
    assert "/x/locked: Permission denied" in text


def test_render_report_no_duplicates():
    text = render_report(ScanResult(roots=["/x"], files_scanned=1, bytes_scanned=5))
    assert "No duplicates found." in text


def test_render_report_limit():
    r = sample_result()
    r.groups = r.groups * 3
    text = render_report(r, limit=1)
    assert "[2]" not in text
    assert "and 2 more groups" in text


def test_result_to_dict_is_json_serializable():
    d = result_to_dict(sample_result())
    again = json.loads(json.dumps(d))
    assert again["wasted_bytes"] == 2048
    assert again["groups"][0]["files"][0]["path"] == "/x/a"
    assert again["errors"] == [{"path": "/x/locked", "error": "Permission denied"}]
