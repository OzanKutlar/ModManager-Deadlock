"""Install mods into citadel/addons as pakNN_dir.vpk files (or park them disabled), and uninstall them.

All VPKs chosen from one archive form a single mod record and are always handled together.
"""

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
DuplicateCheck = Callable[[InstalledMod], bool]


@dataclass(frozen=True)
class InstallRequest:
    archive: Path
    name: str
    gamebanana_mod: int | None = None
    gamebanana_file: int | None = None
    group: str | None = None
    enabled: bool = True
    into_mod_id: str | None = None


@dataclass(frozen=True)
class UninstallResult:
    removed: tuple[str, ...]
    skipped: tuple[str, ...]


# ----------------------------------------------------------------------
# Addons folder inspection & slots
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


def free_slot_names(used: set[int], count: int) -> list[str]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise InstallError('Slot count must be a positive integer.')
    free = [f'pak{n:02d}_dir.vpk' for n in range(MIN_SLOT, MAX_SLOT + 1) if n not in used]
    if len(free) < count:
        raise InstallError(f'Not enough free pakNN_dir.vpk slots ({len(free)} free, {count} needed).')
    return free[:count]


def allocate_slots(addons: Path, count: int) -> list[str]:
    return free_slot_names(used_slots(addons), count)


def slot_numbers(files) -> set[int]:
    """pakNN numbers used by a mod's own file names."""
    numbers: set[int] = set()
    for item in files:
        match = SLOT_RE.match(item.slot)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def find_duplicate(hashes: set[str], mods: list[InstalledMod]) -> InstalledMod | None:
    """An installed mod whose files have exactly these hashes."""
    if not hashes:
        return None
    return next((m for m in mods if {f.sha256 for f in m.files} == hashes), None)


def unmanaged_vpks(addons: Path, mods: list[InstalledMod]) -> list[str]:
    """VPK files in `addons` that no enabled manifest record accounts for."""
    if not addons.is_dir():
        return []
    target = str(addons.resolve()).lower()
    managed = {
        f.slot.lower()
        for m in mods if m.enabled and str(m.addons.resolve()).lower() == target
        for f in m.files
    }
    names = _addon_names(addons)
    return sorted(n for n in names if n.lower().endswith('.vpk') and n.lower() not in managed)


def remove_empty_dir(path: Path) -> None:
    """Best-effort cleanup of an empty parking folder."""
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError as e:
        ui.dim(f'Left folder {escape(str(path))} in place: {escape(str(e))}')


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
    if not isinstance(request.enabled, bool):
        raise InstallError('enabled must be true or false.')


def _choose(vpks: list[ExtractedVpk], choose: VpkChooser) -> list[ExtractedVpk]:
    if len(vpks) == 1:
        return list(vpks)
    chosen = list(choose(vpks))
    allowed = {v.path for v in vpks}
    if any(v.path not in allowed for v in chosen):
        raise InstallError('Selection contains a file that is not in the archive.')
    return chosen


def _ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InstallError(f'Cannot create {path}: {e}') from e


def _undo_copies(target_dir: Path, files) -> None:
    for item in files:
        try:
            (target_dir / item.slot).unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            ui.warn(f'Could not remove partial file {escape(item.slot)}: {escape(str(e))}')


def _copy_into_slots(chosen: list[ExtractedVpk], target_dir: Path, slots: list[str],
                     mod_name: str) -> tuple[InstalledFile, ...]:
    installed: list[InstalledFile] = []
    try:
        for vpk, slot in zip(chosen, slots):
            target = target_dir / slot
            copy_with_backup(vpk.path, target, f'overwritten while installing {mod_name}')
            installed.append(InstalledFile(slot, vpk.relative, sha256_file(target), target.stat().st_size))
    except (BackupError, OSError) as e:
        _undo_copies(target_dir, installed)
        raise InstallError(f'Copying mod files failed: {e}') from e
    return tuple(installed)


def _record(request: InstallRequest, mod_id: str, addons: Path,
            files: tuple[InstalledFile, ...], group: str | None) -> InstalledMod:
    return InstalledMod(
        mod_id=mod_id,
        name=request.name.strip()[:MAX_NAME_LEN],
        source=str(request.archive.resolve()),
        addons_dir=str(addons.resolve()),
        installed=time.time(),
        files=files,
        gamebanana_mod=request.gamebanana_mod,
        gamebanana_file=request.gamebanana_file,
        enabled=request.enabled,
        group=group,
    )


def _patch_gameinfo(game: GamePaths) -> None:
    try:
        if gameinfo.ensure_patched(game.gameinfo):
            ui.ok('Patched gameinfo.gi search paths (backup saved).')
    except GameInfoError as e:
        ui.warn(f'Mod files are installed, but gameinfo.gi could not be patched: {escape(str(e))}')
        ui.warn('Fix the problem, then run: python deadlock_mm.py patch')


def _is_wanted(chosen: list[ExtractedVpk], on_duplicate: DuplicateCheck | None) -> bool:
    if on_duplicate is None:
        return True
    try:
        hashes = {sha256_file(v.path) for v in chosen}
    except OSError as e:
        raise InstallError(f'Cannot read extracted files: {e}') from e
    existing = find_duplicate(hashes, manifest.load_mods())
    return existing is None or bool(on_duplicate(existing))


def _into_target(request: InstallRequest) -> InstalledMod | None:
    if request.into_mod_id is None:
        return None
    target = manifest.find_mod(request.into_mod_id)
    if target is None:
        raise InstallError('The mod to add files to is no longer installed.')
    return target


def _placement(request: InstallRequest, game: GamePaths, mod_id: str, count: int) -> tuple[Path, list[str]]:
    """Target folder and slot names for a new mod."""
    _ensure_dir(game.addons)
    slots = allocate_slots(game.addons, count)
    target_dir = game.addons if request.enabled else manifest.parking_dir(game.addons, mod_id)
    _ensure_dir(target_dir)
    return target_dir, slots


def _placement_into(target: InstalledMod, count: int) -> tuple[Path, list[str]]:
    """Target folder and slot names when appending files to an existing mod."""
    if target.enabled:
        _ensure_dir(target.addons)
        return target.addons, allocate_slots(target.addons, count)
    _ensure_dir(target.parking)
    return target.parking, free_slot_names(slot_numbers(target.files), count)


def _store(request: InstallRequest, game: GamePaths, into: InstalledMod | None,
           files: tuple[InstalledFile, ...], target_dir: Path, mod_id: str, group: str | None) -> InstalledMod:
    if into is not None:
        record, save = replace(into, files=(*into.files, *files)), manifest.replace_mod
    else:
        record, save = _record(request, mod_id, game.addons, files, group), manifest.add_mod
    try:
        save(record)
    except (ConfigError, InstallError) as e:
        _undo_copies(target_dir, files)
        raise InstallError(f'Could not record the install; copied files were removed: {e}') from e
    return record


def install(request: InstallRequest, game: GamePaths, choose: VpkChooser,
            on_duplicate: DuplicateCheck | None = None) -> InstalledMod | None:
    """Extract, copy into slots (addons/ or the parking folder), record, then patch gameinfo.gi.

    With request.into_mod_id the files are appended to that existing mod instead.
    on_duplicate(existing) is asked when the chosen VPKs exactly match an installed mod.
    """
    _validate_request(request)
    into = _into_target(request)
    group = manifest.canonical_group(manifest.load_mods(), request.group)
    mod_id = manifest.new_mod_id()
    with tempfile.TemporaryDirectory(prefix='dlmm-', ignore_cleanup_errors=True) as tmp:
        vpks = extract_vpks(request.archive, Path(tmp))
        if not vpks:
            raise InstallError(f'No .vpk files found inside {request.archive.name}.')
        chosen = _choose(vpks, choose)
        if not chosen or not _is_wanted(chosen, on_duplicate):
            return None
        if into is not None:
            target_dir, slots = _placement_into(into, len(chosen))
        else:
            target_dir, slots = _placement(request, game, mod_id, len(chosen))
        files = _copy_into_slots(chosen, target_dir, slots, request.name)
    record = _store(request, game, into, files, target_dir, mod_id, group)
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
    """Delete (with backup) every file of the mod, enabled or parked, whose hash still matches."""
    if not isinstance(mod, InstalledMod):
        raise InstallError('Invalid mod record.')
    removed: list[str] = []
    skipped: list[str] = []
    failed: list[InstalledFile] = []
    for item in mod.files:
        status, reason = _remove_file(manifest.file_location(mod, item), item, mod.name)
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
    if not mod.enabled:
        remove_empty_dir(mod.parking)
    return UninstallResult(tuple(removed), tuple(skipped))
