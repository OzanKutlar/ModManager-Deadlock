"""Timestamped backups of every file dlmm overwrites or deletes, plus rollback.

Layout: <app_dir>/backups/<backup_id>/<original file name> + backup.json
"""

import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from rich.markup import escape

from . import ui
from .config import app_dir, read_json, write_json
from .errors import BackupError, ConfigError
from .fsutil import atomic_write_text, copy_atomic, is_readonly, set_writable, sha256_file

BACKUP_DIRNAME = 'backups'
META_FILENAME = 'backup.json'
MAX_BACKUPS = 100
MAX_BACKUP_BYTES = 8 * 1024 ** 3
MAX_LIST_ENTRIES = 10_000
MAX_ID_ATTEMPTS = 100
UNSAFE_CHARS_RE = re.compile(r'[^A-Za-z0-9._-]')


@dataclass(frozen=True)
class BackupEntry:
    backup_id: str
    original_path: str
    stored_file: str
    reason: str
    created: float
    sha256: str
    size: int
    pinned: bool = False

    @property
    def stored_path(self) -> Path:
        return backups_root() / self.backup_id / self.stored_file


def backups_root() -> Path:
    root = app_dir() / BACKUP_DIRNAME
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupError(f'Cannot create backup folder {root}: {e}') from e
    return root


def _allocate_folder(label: str) -> tuple[str, Path]:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    safe_label = UNSAFE_CHARS_RE.sub('_', label)[:60] or 'file'
    root = backups_root()
    for attempt in range(MAX_ID_ATTEMPTS):
        backup_id = f'{stamp}-{attempt:02d}-{safe_label}'
        folder = root / backup_id
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        except OSError as e:
            raise BackupError(f'Cannot create backup folder {folder}: {e}') from e
        return backup_id, folder
    raise BackupError('Too many backups created in the same second.')


def backup_file(path: Path, reason: str, pinned: bool = False, prune: bool = True) -> BackupEntry | None:
    """Copy `path` into a new backup slot. Returns None if there is no file to back up."""
    if not isinstance(reason, str) or not reason.strip():
        raise BackupError('A backup reason is required.')
    source = Path(path)
    if not source.is_file():
        return None
    backup_id, folder = _allocate_folder(source.name)
    stored = folder / source.name
    try:
        shutil.copyfile(source, stored)
        set_writable(stored, True)
        entry = BackupEntry(
            backup_id=backup_id,
            original_path=str(source.resolve()),
            stored_file=source.name,
            reason=reason.strip(),
            created=time.time(),
            sha256=sha256_file(stored),
            size=stored.stat().st_size,
            pinned=pinned,
        )
        write_json(folder / META_FILENAME, asdict(entry))
    except (OSError, ConfigError) as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise BackupError(f'Backup of {source} failed: {e}') from e
    if prune:
        _prune()
    return entry


def _load_entry(folder: Path) -> BackupEntry | None:
    try:
        data = read_json(folder / META_FILENAME, None)
    except ConfigError as e:
        ui.warn(f'Skipping unreadable backup {escape(folder.name)}: {escape(str(e))}')
        return None
    if not isinstance(data, dict):
        ui.warn(f'Skipping backup without metadata: {escape(folder.name)}')
        return None
    names = {f.name for f in fields(BackupEntry)}
    try:
        entry = BackupEntry(**{k: v for k, v in data.items() if k in names})
    except TypeError as e:
        ui.warn(f'Skipping malformed backup {escape(folder.name)}: {escape(str(e))}')
        return None
    if entry.backup_id != folder.name:
        ui.warn(f'Skipping backup with mismatched id: {escape(folder.name)}')
        return None
    return entry


def list_backups() -> list[BackupEntry]:
    """All valid backups, newest first."""
    root = backups_root()
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as e:
        raise BackupError(f'Cannot list backups in {root}: {e}') from e
    entries: list[BackupEntry] = []
    for folder in folders[:MAX_LIST_ENTRIES]:
        entry = _load_entry(folder)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: (e.created, e.backup_id), reverse=True)
    return entries


def has_pinned_backup(path: Path) -> bool:
    target = str(Path(path).resolve())
    return any(e.pinned and e.original_path == target for e in list_backups())


def _delete_backup(entry: BackupEntry) -> None:
    folder = backups_root() / entry.backup_id
    try:
        shutil.rmtree(folder)
    except OSError as e:
        ui.warn(f'Could not prune backup {escape(entry.backup_id)}: {escape(str(e))}')


def _prune() -> None:
    """Keep the newest unpinned backups within MAX_BACKUPS / MAX_BACKUP_BYTES (newest always kept)."""
    kept_count = 0
    kept_bytes = 0
    for entry in list_backups():
        if entry.pinned:
            continue
        within_limits = kept_count < MAX_BACKUPS and kept_bytes + entry.size <= MAX_BACKUP_BYTES
        if kept_count == 0 or within_limits:
            kept_count += 1
            kept_bytes += entry.size
            continue
        _delete_backup(entry)


@contextmanager
def _temporarily_writable(target: Path):
    was_readonly = is_readonly(target)
    if was_readonly:
        set_writable(target, True)
    try:
        yield
    finally:
        if was_readonly and target.exists():
            set_writable(target, False)


def copy_with_backup(source: Path, target: Path, reason: str) -> BackupEntry | None:
    """Back up `target` if it exists, then overwrite it with `source`."""
    entry = backup_file(target, reason)
    try:
        with _temporarily_writable(target):
            copy_atomic(source, target)
    except OSError as e:
        raise BackupError(f'Could not write {target}: {e}') from e
    return entry


def write_text_with_backup(target: Path, text: str, reason: str, pinned: bool = False) -> BackupEntry | None:
    """Back up `target` if it exists, then replace its contents with `text`."""
    entry = backup_file(target, reason, pinned=pinned)
    try:
        with _temporarily_writable(target):
            atomic_write_text(target, text)
    except OSError as e:
        raise BackupError(f'Could not write {target}: {e}') from e
    return entry


def delete_with_backup(target: Path, reason: str) -> BackupEntry:
    """Back up `target`, then delete it."""
    entry = backup_file(target, reason)
    if entry is None:
        raise BackupError(f'Nothing to delete at {target}.')
    try:
        if is_readonly(target):
            set_writable(target, True)
        target.unlink()
    except OSError as e:
        raise BackupError(f'Could not delete {target}: {e}') from e
    return entry


def _verify(entry: BackupEntry) -> Path:
    stored = entry.stored_path
    if not stored.is_file():
        raise BackupError(f'Backup file is missing: {stored}')
    try:
        actual = sha256_file(stored)
    except OSError as e:
        raise BackupError(f'Cannot read backup {stored}: {e}') from e
    if actual != entry.sha256:
        raise BackupError('Backup file is corrupted (hash mismatch); refusing to restore.')
    return stored


def restore_backup(entry: BackupEntry) -> BackupEntry | None:
    """Restore a backup to its original location. The current file is backed up first."""
    if not isinstance(entry, BackupEntry):
        raise BackupError('Invalid backup entry.')
    stored = _verify(entry)
    target = Path(entry.original_path)
    safety = backup_file(target, f'before rollback to {entry.backup_id}', prune=False)
    try:
        with _temporarily_writable(target):
            copy_atomic(stored, target)
    except OSError as e:
        raise BackupError(f'Could not restore {target}: {e}') from e
    _prune()
    return safety
