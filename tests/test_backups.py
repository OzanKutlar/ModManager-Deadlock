import pytest

from dlmm import backups
from dlmm.errors import BackupError


def test_backup_and_restore(tmp_path):
    target = tmp_path / 'file.txt'
    target.write_text('v1')
    entry = backups.backup_file(target, 'test')
    target.write_text('v2')
    safety = backups.restore_backup(entry)
    assert target.read_text() == 'v1'
    assert safety is not None and safety.stored_path.read_text() == 'v2'


def test_backup_missing_file_returns_none(tmp_path):
    assert backups.backup_file(tmp_path / 'nope.txt', 'test') is None


def test_prune_keeps_pinned_and_limits_count(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, 'MAX_BACKUPS', 2)
    target = tmp_path / 'f.txt'
    target.write_text('original')
    pinned = backups.backup_file(target, 'original', pinned=True)
    for round_no in range(5):
        target.write_text(str(round_no))
        backups.backup_file(target, f'round {round_no}')
    entries = backups.list_backups()
    assert any(e.backup_id == pinned.backup_id for e in entries)
    assert sum(1 for e in entries if not e.pinned) == 2


def test_corrupted_backup_refuses_restore(tmp_path):
    target = tmp_path / 'f.txt'
    target.write_text('good')
    entry = backups.backup_file(target, 'test')
    entry.stored_path.write_text('tampered')
    with pytest.raises(BackupError):
        backups.restore_backup(entry)
    assert target.read_text() == 'good'


def test_delete_with_backup(tmp_path):
    target = tmp_path / 'pak01_dir.vpk'
    target.write_bytes(b'data')
    entry = backups.delete_with_backup(target, 'uninstall test')
    assert not target.exists()
    backups.restore_backup(entry)
    assert target.read_bytes() == b'data'
