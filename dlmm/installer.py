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

from . import gameinfo, heroes, manifest, ui, units, vpk
from .archives import ExtractedVpk, extract_vpks
from .backups import copy_with_backup, delete_with_backup, set_pinned
from .errors import BackupError, ConfigError, GameInfoError, InstallError, VpkError
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
    hero: str | None = None
    share: bool = False
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


def relocate(files, used: set[int]) -> tuple[InstalledFile, ...]:
    """Keep each _dir file's slot when free, otherwise give it the lowest free one; chunks follow."""
    taken = set(used)
    mapping: dict[str, str] = {}
    pending: list[str] = []
    for item in units.mains(files):
        match = SLOT_RE.match(item.slot)
        number = int(match.group(1)) if match else None
        if number is not None and number not in taken:
            taken.add(number)
            mapping[item.slot] = item.slot
        else:
            pending.append(item.slot)
    if pending:
        mapping.update(zip(pending, free_slot_names(taken, len(pending))))
    return units.renamed(files, mapping)


def verify_files(mod: InstalledMod) -> None:
    """Raise unless every file of the mod still has the hash dlmm recorded."""
    for item in mod.files:
        path = manifest.file_location(mod, item)
        try:
            actual = sha256_file(path)
        except OSError as e:
            raise InstallError(f'Cannot read {path}: {e}') from e
        if actual != item.sha256:
            raise InstallError(f'{mod.name}: {item.slot} changed since install.')


def _unpin(backup_ids) -> None:
    for backup_id in backup_ids:
        try:
            set_pinned(backup_id, False)
        except BackupError as e:
            ui.warn(f'Could not release backup {escape(str(backup_id))}: {escape(str(e))}')


def release_pack_backups(mod: InstalledMod) -> None:
    _unpin(mod.pack_backups)


def release_backups(mod: InstalledMod) -> None:
    """Let the backups kept for a mod's undo options be pruned normally again."""
    heal_ids = [f.heal_backup for f in (*mod.files, *mod.packed_from) if f.heal_backup]
    _unpin([*mod.pack_backups, *heal_ids])


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


def _units_of(vpks: list[ExtractedVpk]) -> list[units.Unit]:
    grouped, orphans = units.group_units(vpks)
    for orphan in orphans:
        ui.warn(f'Ignoring {escape(orphan.relative)}: chunk file without its _dir.vpk.')
    return grouped


def _choose_units(unit_list: list[units.Unit], choose: VpkChooser) -> list[units.Unit]:
    """The chooser sees one entry per loadable VPK; chunk files always travel with it."""
    if len(unit_list) == 1:
        return list(unit_list)
    by_path = {u.main.path: u for u in unit_list}
    picked = list(choose([u.main for u in unit_list]))
    if any(v.path not in by_path for v in picked):
        raise InstallError('Selection contains a file that is not in the archive.')
    return [by_path[v.path] for v in picked]


def _hero_for(request: InstallRequest, chosen: list[units.Unit]) -> str | None:
    if request.hero:
        return manifest.canonical_hero(manifest.load_mods(), request.hero)
    paths: list[str] = []
    for unit in chosen:
        try:
            paths.extend(vpk.list_files(unit.main.path))
        except VpkError:
            continue  # unreadable tree: detection is best effort
    return heroes.describe(paths) if paths else None


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


def _copy_one(source: ExtractedVpk, target: Path, mod_name: str, part_of: str | None) -> InstalledFile:
    copy_with_backup(source.path, target, f'overwritten while installing {mod_name}')
    return InstalledFile(target.name, source.relative, sha256_file(target), target.stat().st_size, part_of=part_of)


def _copy_into_slots(chosen: list[units.Unit], target_dir: Path, slots: list[str],
                     mod_name: str) -> tuple[InstalledFile, ...]:
    installed: list[InstalledFile] = []
    try:
        for unit, slot in zip(chosen, slots):
            installed.append(_copy_one(unit.main, target_dir / slot, mod_name, None))
            for index, chunk in unit.chunks:
                installed.append(_copy_one(chunk, target_dir / units.chunk_slot(slot, index), mod_name, slot))
    except (BackupError, OSError) as e:
        _undo_copies(target_dir, installed)
        raise InstallError(f'Copying mod files failed: {e}') from e
    return tuple(installed)


def _record(request: InstallRequest, mod_id: str, addons: Path,
            files: tuple[InstalledFile, ...], hero: str | None) -> InstalledMod:
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
        hero=hero,
        hero_manual=bool(request.hero),
        exclusive=not request.share,
    )


def _patch_gameinfo(game: GamePaths) -> None:
    try:
        if gameinfo.ensure_patched(game.gameinfo):
            ui.ok('Patched gameinfo.gi search paths (backup saved).')
    except GameInfoError as e:
        ui.warn(f'Mod files are installed, but gameinfo.gi could not be patched: {escape(str(e))}')
        ui.warn('Fix the problem, then run: python deadlock_mm.py patch')


def _is_wanted(chosen: list[units.Unit], on_duplicate: DuplicateCheck | None) -> bool:
    if on_duplicate is None:
        return True
    try:
        hashes = {sha256_file(v.path) for unit in chosen for v in unit.files}
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
           files: tuple[InstalledFile, ...], target_dir: Path, mod_id: str, hero: str | None) -> InstalledMod:
    if into is not None:
        record, save = replace(into, files=(*into.files, *files)), manifest.replace_mod
    else:
        record, save = _record(request, mod_id, game.addons, files, hero), manifest.add_mod
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
    mod_id = manifest.new_mod_id()
    with tempfile.TemporaryDirectory(prefix='dlmm-', ignore_cleanup_errors=True) as tmp:
        unit_list = _units_of(extract_vpks(request.archive, Path(tmp)))
        if not unit_list:
            raise InstallError(f'No .vpk files found inside {request.archive.name}.')
        chosen = _choose_units(unit_list, choose)
        if not chosen or not _is_wanted(chosen, on_duplicate):
            return None
        hero = into.hero if into is not None else _hero_for(request, chosen)
        if into is not None:
            target_dir, slots = _placement_into(into, len(chosen))
        else:
            target_dir, slots = _placement(request, game, mod_id, len(chosen))
        files = _copy_into_slots(chosen, target_dir, slots, request.name)
    record = _store(request, game, into, files, target_dir, mod_id, hero)
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
        release_backups(mod)
    if not mod.enabled:
        remove_empty_dir(mod.parking)
    return UninstallResult(tuple(removed), tuple(skipped))
