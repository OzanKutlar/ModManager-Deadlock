"""Pack a multi-file mod into one VPK with vpkmerge (later parts win where they overlap), with undo.

The original parts are kept as pinned backups so undo_pack can split the mod back.
"""

import tempfile
from dataclasses import replace
from pathlib import Path

from rich.markup import escape

from . import backups, manifest, toggler, ui, units, vpk, vpkmerge
from .backups import BackupEntry, copy_with_backup, delete_with_backup
from .errors import BackupError, ConfigError, InstallError, VpkError, VpkMergeError
from .fsutil import sha256_file
from .installer import relocate, used_slots, verify_files
from .manifest import InstalledFile, InstalledMod

PACKED_NAME = 'packed_dir.vpk'
PACKED_PREFIX = 'packed'
MAX_PARTS = 50
MAX_STAGING_ENTRIES = 1_000

Removed = list[tuple[InstalledFile, BackupEntry]]


def _folder(mod: InstalledMod) -> Path:
    return mod.addons if mod.enabled else mod.parking


def _ordered_mains(mod: InstalledMod, order: list[str] | None) -> list[InstalledFile]:
    mains = units.mains(mod.files)
    if order is None:
        return mains
    by_slot = {m.slot: m for m in mains}
    if sorted(order) != sorted(by_slot):
        raise InstallError('The pack order must list every part exactly once.')
    return [by_slot[slot] for slot in order]


def _paths_of(path: Path) -> frozenset[str]:
    try:
        return vpk.list_files(path)
    except VpkError as e:
        raise VpkMergeError(f'Cannot read {path.name}: {e}') from e


def _verify_output(output: Path, inputs: list[Path]) -> None:
    """The packed VPK must list every file of every input."""
    expected = frozenset().union(*(_paths_of(p) for p in inputs))
    missing = expected - _paths_of(output)
    if missing:
        sample = ', '.join(sorted(missing)[:3])
        raise VpkMergeError(f'The packed VPK is missing {len(missing)} file(s) (e.g. {sample}); originals kept.')


def _produced(output: Path) -> list[tuple[str | None, Path]]:
    """The packed _dir.vpk plus any chunk files vpkmerge wrote next to it, as (chunk index, path)."""
    found: list[tuple[str | None, Path]] = [(None, output)]
    for count, path in enumerate(sorted(output.parent.iterdir())):
        if count >= MAX_STAGING_ENTRIES:
            break
        match = units.CHUNK_RE.match(path.name)
        if match and match.group('prefix').lower() == PACKED_PREFIX:
            found.append((match.group('index'), path))
    return found


def _restore_removed(folder: Path, removed: Removed) -> None:
    for item, entry in reversed(removed):
        try:
            backups.restore_backup_to(entry, folder / item.slot, 'pack rollback')
            backups.set_pinned(entry.backup_id, False)
        except BackupError as e:
            ui.warn(f'Could not put back {escape(item.slot)}: {escape(str(e))}')


def _remove_all(mod: InstalledMod, folder: Path, reason: str, pinned: bool) -> Removed:
    removed: Removed = []
    try:
        for item in mod.files:
            removed.append((item, delete_with_backup(folder / item.slot, reason, pinned=pinned)))
    except BackupError as e:
        _restore_removed(folder, removed)
        raise InstallError(f'Could not set the current files aside: {e}') from e
    return removed


def _discard_placed(folder: Path, files) -> None:
    for item in files:
        try:
            (folder / item.slot).unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            ui.warn(f'Could not remove {escape(item.slot)}: {escape(str(e))}')


def _place_packed(produced: list[tuple[str | None, Path]], folder: Path, slot: str) -> tuple[InstalledFile, ...]:
    placed: list[InstalledFile] = []
    try:
        for index, source in produced:
            name = slot if index is None else units.chunk_slot(slot, index)
            target = folder / name
            copy_with_backup(source, target, 'overwritten while packing')
            part_of = None if index is None else slot
            placed.append(InstalledFile(name, 'packed by vpkmerge', sha256_file(target), target.stat().st_size, part_of=part_of))
    except (BackupError, OSError) as e:
        _discard_placed(folder, placed)
        raise InstallError(f'Could not place the packed VPK: {e}') from e
    return tuple(placed)


def _commit(updated: InstalledMod, folder: Path, placed, removed: Removed) -> InstalledMod:
    try:
        manifest.replace_mod(updated)
    except ConfigError as e:
        _discard_placed(folder, placed)
        _restore_removed(folder, removed)
        raise InstallError(f'Could not save the change; it was rolled back: {e}') from e
    return updated


def pack(mod: InstalledMod, order: list[str] | None = None) -> InstalledMod:
    """Merge the mod's VPKs into one. `order` lists part slots lowest priority first (last wins)."""
    current = toggler.fresh(mod)
    if current.packed_from:
        raise InstallError(f'{current.name} is already packed; undo that first.')
    mains = _ordered_mains(current, order)
    if not 2 <= len(mains) <= MAX_PARTS:
        raise InstallError(f'Packing needs a mod with 2 to {MAX_PARTS} VPKs.')
    verify_files(current)
    folder = _folder(current)
    inputs = [folder / m.slot for m in mains]
    with tempfile.TemporaryDirectory(prefix='dlmm-pack-', ignore_cleanup_errors=True) as tmp:
        output = Path(tmp) / PACKED_NAME
        vpkmerge.merge_vpks(output, inputs)
        _verify_output(output, inputs)
        produced = _produced(output)
        slot = units.mains(current.files)[0].slot
        removed = _remove_all(current, folder, f'packed into one VPK ({current.name})', pinned=True)
        try:
            placed = _place_packed(produced, folder, slot)
        except InstallError:
            _restore_removed(folder, removed)
            raise
    updated = replace(
        current, files=placed, packed_from=current.files,
        pack_backups=tuple(entry.backup_id for _item, entry in removed),
    )
    return _commit(updated, folder, placed, removed)


# ----------------------------------------------------------------------
# Undo
# ----------------------------------------------------------------------

def _pack_entries(mod: InstalledMod) -> list[BackupEntry]:
    entries = [backups.find_backup(i) for i in mod.pack_backups]
    if len(entries) != len(mod.packed_from) or any(e is None for e in entries):
        raise InstallError('Some original parts are no longer in the backups; the pack cannot be undone.')
    for item, entry in zip(mod.packed_from, entries):
        if entry.sha256 != item.sha256:
            raise InstallError(f'The backup of {item.slot} does not match the original; not undoing.')
    return entries


def _restore_parts(parts, entries: list[BackupEntry], folder: Path, reason: str) -> None:
    restored: list[InstalledFile] = []
    try:
        for item, entry in zip(parts, entries):
            backups.restore_backup_to(entry, folder / item.slot, reason)
            restored.append(item)
    except BackupError as e:
        _discard_placed(folder, restored)
        raise InstallError(f'Could not restore the original parts: {e}') from e


def undo_pack(mod: InstalledMod) -> InstalledMod:
    """Replace the packed VPK with the original parts again."""
    current = toggler.fresh(mod)
    if not current.packed_from:
        raise InstallError(f'{current.name} is not packed.')
    entries = _pack_entries(current)
    verify_files(current)
    folder = _folder(current)
    reason = f'undo pack of {current.name}'
    removed = _remove_all(current, folder, reason, pinned=False)
    parts = relocate(current.packed_from, used_slots(folder) if current.enabled else set())
    try:
        _restore_parts(parts, entries, folder, reason)
    except InstallError:
        _restore_removed(folder, removed)
        raise
    updated = _commit(replace(current, files=parts, packed_from=(), pack_backups=()), folder, parts, removed)
    for entry in entries:
        try:
            backups.set_pinned(entry.backup_id, False)
        except BackupError as e:
            ui.warn(f'Could not release backup {escape(entry.backup_id)}: {escape(str(e))}')
    return updated
