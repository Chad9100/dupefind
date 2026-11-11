import json
import os

from dupefind import scanner
from dupefind.cli import main
from dupefind.scanner import find_duplicates


def make_tree(base):
    (base / "mnt").mkdir(parents=True)
    (base / "a.txt").write_text("same content", encoding="utf-8")
    (base / "mnt" / "b.txt").write_text("same content", encoding="utf-8")
    return base


def fake_mount(monkeypatch, mount_name):
    real = scanner._device

    def device(path):
        if os.path.basename(path) == mount_name:
            return 424242
        return real(path)

    monkeypatch.setattr(scanner, "_device", device)


def test_default_enters_everything(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "data")
    fake_mount(monkeypatch, "mnt")
    result = find_duplicates([str(root)])
    assert result.files_scanned == 2
    assert len(result.groups) == 1


def test_one_file_system_skips_other_device(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "data")
    fake_mount(monkeypatch, "mnt")
    result = find_duplicates([str(root)], one_file_system=True)
    assert result.files_scanned == 1
    assert result.groups == []


def test_one_file_system_same_device_unchanged(tmp_path):
    root = make_tree(tmp_path / "data")
    result = find_duplicates([str(root)], one_file_system=True)
    assert result.files_scanned == 2
    assert len(result.groups) == 1


def test_cli_flag(tmp_path, monkeypatch, capsys):
    root = make_tree(tmp_path / "data")
    fake_mount(monkeypatch, "mnt")
    assert main(["scan", str(root), "--json", "--one-file-system"]) == 0
    assert json.loads(capsys.readouterr().out)["groups"] == []
    assert main(["scan", str(root), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["groups"]) == 1
