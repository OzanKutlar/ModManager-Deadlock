"""Violet Fix mod: heal mods with stale camera attachments (Deadlock's Violet update) via vpkmerge.

Per file, as in Deadlocker Mod Fixer by impyrobot (MIT): check -> heal into a staging file ->
verify the result is clean -> back up the original (pinned, for undo) -> replace -> update the manifest.
"""

import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from rich.markup import escape

from . import backups, manifest, toggler, ui, vpkmerge
from .backups import BackupEntry, copy_with_backup
from .errors import BackupError, ConfigError, InstallError, VpkMergeError
from .fsutil import sha256_file
from .game_paths import GamePaths
from .manifest import InstalledFile, InstalledMod, file_location

BASE_PAK = 'pak01_dir.vpk'
CURRENT, STALE, SPLIT, CHANGED, ERROR = 'current', 'stale', 'split', 'changed', 'error'
SEVERITY = (ERROR, CHANGED, STALE, SPLIT, CURRENT)
GAME_PROCESS = 'project8.exe'
TASKLIST_TIMEOUT = 15
MAX_MODS = 1_000

Healed = dict[str, tuple[InstalledFile, BackupEntry]]


@dataclass(frozen=True)
class FileCheck:
    slot: str
    status: str
    stale_models: tuple[str, ...] = ()
    detail: str = ''


@dataclass(frozen=True)
class ModCheck:
    mod: InstalledMod
    files: tuple[FileCheck, ...]

    @property
    def status(self) -> str:
        present = {f.status for f in self.files}
        return next((s for s in SEVERITY if s in present), CURRENT)

    @property
    def stale_files(self) -> tuple[FileCheck, ...]:
        return tuple(f for f in self.files if f.status == STALE)

    @property
    def summary(self) -> str:
        if self.status in (ERROR, CHANGED):
            first = next(f for f in self.files if f.status == self.status)
            return f'{self.status}: {first.detail}'
        if self.status == STALE:
            models = sum(len(f.stale_models) or 1 for f in self.stale_files)
            return f'stale ({models} model(s))'
        if self.status == SPLIT:
            return 'split across several files - not supported'
        return 'current'


@dataclass(frozen=True)
class FixResult:
    mod: InstalledMod
    fixed: tuple[str, ...]
    notes: tuple[str, ...]


def base_pak(game: GamePaths) -> Path:
    path = game.citadel / BASE_PAK
    if not path.is_file():
        raise VpkMergeError(f'Base game pak not found: {path}')
    return path


def game_running() -> bool | None:
    """True/False on Windows when tasklist answers, None when it cannot be determined."""
    if sys.platform != 'win32':
        return None
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        done = subprocess.run(
            ['tasklist', '/FI', f'IMAGENAME eq {GAME_PROCESS}', '/NH'],
            capture_output=True, text=True, timeout=TASKLIST_TIMEOUT, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return GAME_PROCESS in (done.stdout or '').lower()


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------

def _is_split(mod: InstalledMod, item: InstalledFile) -> bool:
    return bool(item.part_of) or any(f.part_of == item.slot for f in mod.files)


def _check_file(base: Path, mod: InstalledMod, item: InstalledFile) -> FileCheck:
    if _is_split(mod, item):
        return FileCheck(item.slot, SPLIT, detail='split across several files')
    path = file_location(mod, item)
    try:
        if sha256_file(path) != item.sha256:
            return FileCheck(item.slot, CHANGED, detail='changed since install')
        result = vpkmerge.check(base, path)
    except OSError as e:
        return FileCheck(item.slot, ERROR, detail=str(e))
    except VpkMergeError as e:
        return FileCheck(item.slot, ERROR, detail=str(e))
    return FileCheck(item.slot, STALE if result.stale else CURRENT, result.stale_models)


def scan(mod: InstalledMod, game: GamePaths) -> ModCheck:
    """Check every loadable VPK of the mod (read-only)."""
    base = base_pak(game)
    vpkmerge.ensure_tool()
    return ModCheck(mod, tuple(_check_file(base, mod, f) for f in mod.files if not f.part_of))


# ----------------------------------------------------------------------
# Fix
# ----------------------------------------------------------------------

def _heal_one(base: Path, mod: InstalledMod, item: InstalledFile, staging: Path) -> tuple[InstalledFile, BackupEntry]:
    path = file_location(mod, item)
    output = staging / f'healed_{item.slot}'
    vpkmerge.heal(base, path, output)
    entry = copy_with_backup(output, path, f'before Violet fix of {mod.name}', pinned=True)
    if entry is None:
        raise BackupError(f'{path.name} vanished before it could be backed up.')
    if item.heal_backup:
        backups.set_pinned(entry.backup_id, False)  # the very first original is already kept
    healed = replace(
        item,
        sha256=sha256_file(path),
        size=path.stat().st_size,
        healed_from=item.healed_from or item.sha256,
        heal_backup=item.heal_backup or entry.backup_id,
    )
    return healed, entry


def _rollback_heals(current: InstalledMod, healed: Healed) -> None:
    by_slot = {f.slot: f for f in current.files}
    for slot, (_item, entry) in healed.items():
        try:
            backups.restore_backup_to(entry, file_location(current, by_slot[slot]), 'Violet fix rollback')
        except BackupError as e:
            ui.warn(f'Could not put back {escape(slot)}: {escape(str(e))}')


def _save_heals(current: InstalledMod, healed: Healed) -> InstalledMod:
    files = tuple(healed[f.slot][0] if f.slot in healed else f for f in current.files)
    updated = replace(current, files=files)
    try:
        manifest.replace_mod(updated)
    except ConfigError as e:
        _rollback_heals(current, healed)
        raise InstallError(f'Could not save the fix; the originals were put back: {e}') from e
    return updated


def fix(mod: InstalledMod, game: GamePaths) -> FixResult:
    """Heal every stale file of the mod; files that fail stay untouched and are reported."""
    current = toggler.fresh(mod)
    report = scan(current, game)
    base = base_pak(game)
    notes = [f'{f.slot}: {f.detail or f.status}' for f in report.files if f.status not in (STALE, CURRENT)]
    healed: Healed = {}
    by_slot = {f.slot: f for f in current.files}
    with tempfile.TemporaryDirectory(prefix='dlmm-violet-', ignore_cleanup_errors=True) as tmp:
        for file_check in report.stale_files:
            try:
                healed[file_check.slot] = _heal_one(base, current, by_slot[file_check.slot], Path(tmp))
            except (VpkMergeError, BackupError, OSError) as e:
                notes.append(f'{file_check.slot}: {e}')
    updated = _save_heals(current, healed) if healed else current
    return FixResult(updated, tuple(healed), tuple(notes))


# ----------------------------------------------------------------------
# Undo
# ----------------------------------------------------------------------

def _undo_plan(mod: InstalledMod) -> list[tuple[InstalledFile, BackupEntry]]:
    plan = []
    for item in mod.files:
        if not item.healed_from:
            continue
        entry = backups.find_backup(item.heal_backup) if item.heal_backup else None
        if entry is None:
            raise InstallError(f'The original of {item.slot} is no longer in the backups.')
        if entry.sha256 != item.healed_from:
            raise InstallError(f'The backup of {item.slot} does not match its original.')
        try:
            actual = sha256_file(file_location(mod, item))
        except OSError as e:
            raise InstallError(f'Cannot read {item.slot}: {e}') from e
        if actual != item.sha256:
            raise InstallError(f'{item.slot} changed since the fix; not undoing.')
        plan.append((item, entry))
    if not plan:
        raise InstallError(f'{mod.name} has no Violet fix to undo.')
    return plan


def _save_restored(current: InstalledMod, restored: dict[str, InstalledFile]) -> InstalledMod:
    updated = replace(current, files=tuple(restored.get(f.slot, f) for f in current.files))
    manifest.replace_mod(updated)
    return updated


def undo(mod: InstalledMod) -> InstalledMod:
    """Put the original (pre-fix) files back and restore their hashes in the manifest."""
    current = toggler.fresh(mod)
    plan = _undo_plan(current)
    restored: dict[str, InstalledFile] = {}
    try:
        for item, entry in plan:
            backups.restore_backup_to(entry, file_location(current, item), f'before undoing Violet fix of {current.name}')
            restored[item.slot] = replace(item, sha256=entry.sha256, size=entry.size, healed_from=None, heal_backup=None)
    except BackupError as e:
        if restored:
            _save_restored(current, restored)
        raise InstallError(f'Undo stopped part-way: {e}') from e
    updated = _save_restored(current, restored)
    for _item, entry in plan:
        try:
            backups.set_pinned(entry.backup_id, False)
        except BackupError as e:
            ui.warn(f'Could not release backup {escape(entry.backup_id)}: {escape(str(e))}')
    return updated
