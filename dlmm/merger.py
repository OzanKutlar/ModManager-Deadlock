"""Combine several installed mods into one record (e.g. a skin and its separately downloaded voicelines).

Merged-in mods take on the target's enabled state; every move is hash-checked and any failure
rolls back all steps. The result keeps the target's id, character and state.
"""

from dataclasses import replace

from rich.markup import escape

from . import manifest, toggler, ui, units
from .errors import ConfigError, InstallError
from .fileops import MovePlan, move_all, undo_moves
from .fsutil import sha256_file
from .installer import free_slot_names, release_pack_backups, remove_empty_dir, slot_numbers
from .manifest import InstalledFile, InstalledMod, file_location

MAX_SOURCES = 50
MAX_NAME_LEN = 120

Renames = dict[str, tuple[InstalledFile, ...]]


def merge_candidates(mods: list[InstalledMod], target: InstalledMod) -> list[tuple[InstalledMod, bool]]:
    """Other mods as (mod, similar_name), most shared name words first."""
    target_words = toggler.name_words(target.name)
    scored = []
    for mod in mods:
        if mod.mod_id == target.mod_id:
            continue
        shared = len(target_words & toggler.name_words(mod.name))
        scored.append((-shared, mod.name.casefold(), mod, shared > 0))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [(mod, similar) for _score, _name, mod, similar in scored]


def _verify(mod: InstalledMod) -> None:
    for item in mod.files:
        path = file_location(mod, item)
        try:
            actual = sha256_file(path)
        except OSError as e:
            raise InstallError(f'Cannot read {path}: {e}') from e
        if actual != item.sha256:
            raise InstallError(f'{mod.name}: {item.slot} changed since install; not combining.')


def _validate(target: InstalledMod, sources: list[InstalledMod]) -> list[InstalledMod]:
    if not sources:
        raise InstallError('Choose at least one mod to combine.')
    if len(sources) > MAX_SOURCES:
        raise InstallError(f'Too many mods to combine at once (max {MAX_SOURCES}).')
    ids = [s.mod_id for s in sources]
    if target.mod_id in ids:
        raise InstallError('A mod cannot be combined with itself.')
    if len(set(ids)) != len(ids):
        raise InstallError('The same mod was selected twice.')
    current = [toggler.fresh(s) for s in sources]
    for mod in current:
        _verify(mod)
    return current


def _restore_states(originals: list[InstalledMod]) -> None:
    for original in originals:
        try:
            toggler.move_to_state(original, original.enabled)
        except InstallError as e:
            ui.warn(f'Could not restore {escape(original.name)}: {escape(str(e))}')


def _align_states(target: InstalledMod, sources: list[InstalledMod]) -> tuple[list[InstalledMod], list[InstalledMod]]:
    """Move each source to the target's state. Returns (aligned records, originals that changed)."""
    aligned: list[InstalledMod] = []
    changed: list[InstalledMod] = []
    try:
        for source in sources:
            if source.enabled != target.enabled:
                changed.append(source)
            aligned.append(toggler.move_to_state(source, target.enabled))
    except InstallError:
        _restore_states(changed)
        raise
    return aligned, changed


def _park_plan(target: InstalledMod, sources: list[InstalledMod]) -> tuple[MovePlan, Renames]:
    """For a disabled target: move parked source files into its parking folder under unused names."""
    if target.enabled:
        return [], {s.mod_id: s.files for s in sources}
    count = sum(len(units.mains(s.files)) for s in sources)
    names = iter(free_slot_names(slot_numbers(target.files), count))
    plan: MovePlan = []
    renames: Renames = {}
    for source in sources:
        mapping = {item.slot: next(names) for item in units.mains(source.files)}
        new_files = units.renamed(source.files, mapping)
        renames[source.mod_id] = new_files
        plan.extend(
            (source.parking / old.slot, target.parking / new.slot, old.sha256)
            for old, new in zip(source.files, new_files)
        )
    return plan, renames


def _move_parked(target: InstalledMod, aligned: list[InstalledMod],
                 changed: list[InstalledMod]) -> tuple[MovePlan, Renames]:
    try:
        plan, renames = _park_plan(target, aligned)
        move_all(plan)
    except InstallError:
        _restore_states(changed)
        raise
    return plan, renames


def _combined_files(target: InstalledMod, sources: list[InstalledMod], renames: Renames) -> tuple[InstalledFile, ...]:
    extra = [item for source in sources for item in renames[source.mod_id]]
    return (*target.files, *extra)


def _clean_name(new_name: str | None, fallback: str) -> str:
    if isinstance(new_name, str) and new_name.strip():
        return ' '.join(new_name.split())[:MAX_NAME_LEN]
    return fallback


def _save(record: InstalledMod, source_ids: set[str]) -> None:
    mods = [
        record if m.mod_id == record.mod_id else m
        for m in manifest.load_mods() if m.mod_id not in source_ids
    ]
    manifest.save_mods(mods)


def merge(target: InstalledMod, sources: list[InstalledMod], new_name: str | None = None) -> InstalledMod:
    """Fold `sources` into `target`; the result keeps the target's id, group and state."""
    current = toggler.fresh(target)
    fresh_sources = _validate(current, list(sources))
    aligned, changed = _align_states(current, fresh_sources)
    plan, renames = _move_parked(current, aligned, changed)
    packed = [m for m in (current, *aligned) if m.packed_from]
    record = replace(
        current,
        name=_clean_name(new_name, current.name),
        files=_combined_files(current, aligned, renames),
        packed_from=(),
        pack_backups=(),
    )
    try:
        _save(record, {s.mod_id for s in aligned})
    except (ConfigError, InstallError) as e:
        undo_moves(plan)
        _restore_states(changed)
        raise InstallError(f'Could not save the combined mod; changes were rolled back: {e}') from e
    if not current.enabled:
        for source in aligned:
            remove_empty_dir(source.parking)
    for mod in packed:
        release_pack_backups(mod)
    if packed:
        ui.warn('Combining ended the earlier pack; pack --undo is no longer available for it.')
    return record
