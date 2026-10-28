"""Install mods into citadel/addons as pakNN_dir.vpk files, and uninstall them again."""

import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from rich.markup import escape

from . import gameinfo, manifest, ui
from .archives import ExtractedVpk, extract_vpks
from .backups import copy_with_backup, delete_with_backup
from .errors import BackupError, ConfigError, GameInfoError, InstallError
from .fsutil import sha256_file
from .game_paths import GamePaths
from .manifest import InstalledFile, InstalledMod

SLOT_RE = re.compile(r'^pak(\d{2})_dir\.vpk$', re.IGNORECASE)
MIN_SLOT = 1
MAX_SLOT = 99
MAX_ADDON_ENTRIES = 10_000
MAX_NAME_LEN = 120
REMOVED, SKIPPED, FAILED = 'removed', 'skipped', 'failed'

VpkChooser = Callable[[list[ExtractedVpk]], list[ExtractedVpk]]


@dataclass(frozen=True)
class InstallRequest:
    archive: Path
    name: str
    gamebanana_mod: int | None = None
    gamebanana_file: int | None = None


@dataclass(frozen=True)
class UninstallResult:
    removed: tuple[str, ...]
    skipped: tuple[str, ...]


# ----------------------------------------------------------------------
# Addons folder inspection
# ----------------------------------------------------------------------

def _addon_names(addons: Path) -> list[str]:
    if not addons.is_dir():
        return []
    names: list[str] = []
    try:
        with os.scandir(addons) as entries:
            for count, entry in enumerate(entries):
                if count >= MAX_ADDON_ENTRIES:
                    break
                if entry.is_file():
                    names.append(entry.name)
    except OSError as e:
        raise InstallError(f'Cannot read {addons}: {e}') from e
    return names


def used_slots(addons: Path) -> set[int]:
    used: set[int] = set()
    for name in _addon_names(addons):
        match = SLOT_RE.match(name)
        if match:
            used.add(int(match.group(1)))
    return used


def allocate_slots(addons: Path, count: int) -> list[str]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise InstallError('Slot count must be a positive integer.')
    used = used_slots(addons)
    free = [f'pak{n:02d}_dir.vpk' for n in range(MIN_SLOT, MAX_SLOT + 1) if n not in used]
    if len(free) < count:
        raise InstallError(f'Not enough free pakNN_dir.vpk slots ({len(free)} free, {count} needed).')
    return free[:count]


def unmanaged_vpks(addons: Path, mods: list[InstalledMod]) -> list[str]:
    """VPK files in `addons` that no manifest record accounts for."""
    if not addons.is_dir():
        return []
    target = str(addons.resolve()).lower()
    managed = {
        f.slot.lower()
        for m in mods if str(Path(m.addons_dir).resolve()).lower() == target
        for f in m.files
    }
    names = _addon_names(addons)
    return sorted(n for n in names if n.lower().endswith('.vpk') and n.lower() not in managed)


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------

def _validate_request(request: InstallRequest) -> None:
    if not isinstance(request, InstallRequest):
        raise InstallError('Invalid install request.')
    if not request.archive.is_file():
        raise InstallError(f'Archive not found: {request.archive}')
    if not request.name.strip():
        raise InstallError('Mod name must not be empty.')


def _choose(vpks: list[ExtractedVpk], choose: VpkChooser) -> list[ExtractedVpk]:
    if len(vpks) == 1:
        return list(vpks)
    chosen = list(choose(vpks))
    allowed = {v.path for v in vpks}
    if any(v.path not in allowed for v in chosen):
        raise InstallError('Selection contains a file that is not in the archive.')
    return chosen


def _ensure_addons_dir(addons: Path) -> None:
    try:
        addons.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InstallError(f'Cannot create {addons}: {e}') from e


def _undo_copies(addons: Path, files) -> None:
    for item in files:
        try:
            (addons / item.slot).unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            ui.warn(f'Could not remove partial file {escape(item.slot)}: {escape(str(e))}')


def _copy_into_slots(chosen: list[ExtractedVpk], addons: Path, mod_name: str) -> tuple[InstalledFile, ...]:
    slots = allocate_slots(addons, len(chosen))
    installed: list[InstalledFile] = []
    try:
        for vpk, slot in zip(chosen, slots):
            target = addons / slot
            copy_with_backup(vpk.path, target, f'overwritten while installing {mod_name}')
            installed.append(InstalledFile(slot, vpk.relative, sha256_file(target), target.stat().st_size))
    except (BackupError, OSError) as e:
        _undo_copies(addons, installed)
        raise InstallError(f'Copying into addons failed: {e}') from e
    return tuple(installed)


def _record(request: InstallRequest, addons: Path, files: tuple[InstalledFile, ...]) -> InstalledMod:
    return InstalledMod(
        mod_id=manifest.new_mod_id(),
        name=request.name.strip()[:MAX_NAME_LEN],
        source=str(request.archive.resolve()),
        addons_dir=str(addons.resolve()),
        installed=time.time(),
        files=files,
        gamebanana_mod=request.gamebanana_mod,
        gamebanana_file=request.gamebanana_file,
    )


def _patch_gameinfo(game: GamePaths) -> None:
    try:
        if gameinfo.ensure_patched(game.gameinfo):
            ui.ok('Added citadel/addons to gameinfo.gi (backup saved).')
    except GameInfoError as e:
        ui.warn(f'Mod files are installed, but gameinfo.gi could not be patched: {escape(str(e))}')
        ui.warn('Fix the problem, then run: python deadlock_mm.py patch')


def install(request: InstallRequest, game: GamePaths, choose: VpkChooser) -> InstalledMod | None:
    """Extract, copy into free slots, record, then make sure gameinfo.gi loads addons."""
    _validate_request(request)
    with tempfile.TemporaryDirectory(prefix='dlmm-', ignore_cleanup_errors=True) as tmp:
        vpks = extract_vpks(request.archive, Path(tmp))
        if not vpks:
            raise InstallError(f'No .vpk files found inside {request.archive.name}.')
        chosen = _choose(vpks, choose)
        if not chosen:
            return None
        _ensure_addons_dir(game.addons)
        files = _copy_into_slots(chosen, game.addons, request.name)
    record = _record(request, game.addons, files)
    try:
        manifest.add_mod(record)
    except (ConfigError, InstallError) as e:
        _undo_copies(game.addons, files)
        raise InstallError(f'Could not record the install; copied files were removed: {e}') from e
    _patch_gameinfo(game)
    return record


# ----------------------------------------------------------------------
# Uninstall
# ----------------------------------------------------------------------

def _remove_file(path: Path, item: InstalledFile, mod_name: str) -> tuple[str, str]:
    if not path.is_file():
        return SKIPPED, 'already missing'
    try:
        if sha256_file(path) != item.sha256:
            return SKIPPED, 'changed since install - left in place'
        delete_with_backup(path, f'uninstall {mod_name}')
    except (OSError, BackupError) as e:
        return FAILED, f'could not remove: {e}'
    return REMOVED, ''


def uninstall(mod: InstalledMod) -> UninstallResult:
    """Delete (with backup) every file the mod installed whose hash still matches."""
    if not isinstance(mod, InstalledMod):
        raise InstallError('Invalid mod record.')
    addons = Path(mod.addons_dir)
    removed: list[str] = []
    skipped: list[str] = []
    failed: list[InstalledFile] = []
    for item in mod.files:
        status, reason = _remove_file(addons / item.slot, item, mod.name)
        if status == REMOVED:
            removed.append(item.slot)
            continue
        skipped.append(f'{item.slot}: {reason}')
        if status == FAILED:
            failed.append(item)
    if failed:
        manifest.replace_mod(replace(mod, files=tuple(failed)))
    else:
        manifest.remove_mod(mod.mod_id)
    return UninstallResult(tuple(removed), tuple(skipped))
