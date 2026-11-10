import json
import os
from datetime import datetime, timezone

import pytest

from dupefind.plan import (
    PlanError,
    build_plan,
    choose_keep,
    load_plan,
    validate_plan,
    write_plan,
)
from dupefind.scanner import DupGroup, FileInfo, ScanResult

FILES = [
    FileInfo(os.path.join("r1", "deep", "folder", "a.txt"), 10, 300.0, 0),
    FileInfo(os.path.join("r2", "b.txt"), 10, 100.0, 1),
    FileInfo(os.path.join("r2", "zz", "c.txt"), 10, 200.0, 1),
]


def test_keep_oldest():
    assert choose_keep(FILES, "oldest") is FILES[1]


def test_keep_newest():
    assert choose_keep(FILES, "newest") is FILES[0]


def test_keep_shortest_path():
    assert choose_keep(FILES, "shortest-path") is FILES[1]


def test_keep_first_in():
    assert choose_keep(FILES, "first-in") is FILES[0]


def test_keep_ties_are_deterministic():
    a = FileInfo("b", 1, 5.0, 0)
    b = FileInfo("a", 1, 5.0, 0)
    assert choose_keep([a, b], "oldest") is b
    assert choose_keep([b, a], "oldest") is b


def test_unknown_strategy():
    with pytest.raises(ValueError):
        choose_keep(FILES, "random")


def make_plan():
    result = ScanResult(roots=["r1", "r2"], groups=[DupGroup(10, "0" * 64, list(FILES))])
    return build_plan(result, "oldest", now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))


def test_build_plan_structure():
    plan = make_plan()
    assert plan["dupefind_plan"] == 1
    assert plan["created"] == "2026-01-02T03:04:05Z"
    assert plan["keep_strategy"] == "oldest"
    g = plan["groups"][0]
    assert g["keep"] == FILES[1].path
    assert g["remove"] == [FILES[0].path, FILES[2].path]
    assert plan["summary"] == {"groups": 1, "files_to_remove": 2, "reclaimable_bytes": 20}
    validate_plan(plan)


def test_write_and_load_roundtrip(tmp_path):
    path = tmp_path / "plan.json"
    plan = make_plan()
    write_plan(plan, str(path))
    assert load_plan(str(path)) == plan
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


def test_write_refuses_overwrite(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text("old")
    with pytest.raises(FileExistsError):
        write_plan(make_plan(), str(path))
    assert path.read_text() == "old"
    write_plan(make_plan(), str(path), force=True)
    assert json.loads(path.read_text())["dupefind_plan"] == 1


def test_load_missing_file(tmp_path):
    with pytest.raises(PlanError, match="cannot read"):
        load_plan(str(tmp_path / "none.json"))


def test_load_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(PlanError, match="not valid JSON"):
        load_plan(str(p))


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda p: p.pop("dupefind_plan"), "not a dupefind plan"),
        (lambda p: p.update(dupefind_plan=99), "unsupported plan format"),
        (lambda p: p.update(groups={}), "'groups' must be a list"),
        (lambda p: p["groups"][0].update(size=-1), "'size'"),
        (lambda p: p["groups"][0].update(sha256="xyz"), "'sha256'"),
        (lambda p: p["groups"][0].update(keep=""), "'keep'"),
        (lambda p: p["groups"][0].update(remove="a"), "'remove'"),
    ],
)
def test_validate_rejects_malformed(mutate, message):
    plan = make_plan()
    mutate(plan)
    with pytest.raises(PlanError, match=message):
        validate_plan(plan)


def test_validate_rejects_non_object():
    with pytest.raises(PlanError):
        validate_plan([1, 2])
