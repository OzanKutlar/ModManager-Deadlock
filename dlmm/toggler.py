"""Enable/disable mods by moving their VPKs between addons/ and dlmm_disabled/, and manage groups.

Only one mod per group may be enabled: enabling one disables the other members.
Every move is hash-checked and all-or-nothing per mod (partial moves are rolled back).
"""

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from rich.markup import escape

from . import manifest, ui
from .errors import ConfigError, InstallError
from .fsutil import copy_atomic, sha256_file
from .installer import SLOT_RE, free_slot_names, remove_empty_dir, used_slots
from .manifest import InstalledMod

MIN_WORD_LEN = 3
MIN_SUBSTRING_LEN = 4
WORD_RE = re.compile(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+')
STOP_WORDS = frozenset({
    'skin', 'skins', 'mod', 'mods', 'the', 'and', 'for', 'new', 'vpk', 'pak', 'dir', 'alt',
    'version', 'with', 'remake', 'remastered', 'pack', 'zip', 'rar', 'final', 'fix', 'fixed',
    'update', 'updated', 'edition', 'custom', 'replacement', 'replace', 'model', 'models',
})

MovePlan = list[tuple[Path, Path, str]]


@dataclass(frozen=True)
class ToggleResult:
    mod: InstalledMod
    disabled_others: tuple[InstalledMod, ...]


def _fresh(mod: InstalledMod) -> InstalledMod:
    if not isinstance(mod, InstalledMod):
        raise InstallError('Invalid mod record.')
    current = manifest.find_mod(mod.mod_id)
    if current is None:
        raise InstallError(f'{mod.name} is no longer installed.')
    return current


# ----------------------------------------------------------------------
# File moves
# ----------------------------------------------------------------------

def _copy_verify_unlink(source: Path, target: Path, expected_sha: str) -> None:
    """Fallback when a rename is impossible (e.g. different drives)."""
    copy_atomic(source, target)
    if sha256_file(target) != expected_sha:
        target.unlink()
        raise InstallError(f'Copy of {source.name} is corrupt; original left in place.')
    source.unlink()


def _move_file(source: Path, target: Path, expected_sha: str) -> None:
    if not source.is_file():
        raise InstallError(f'Missing file: {source}')
    if target.exists():
        raise InstallError(f'Refusing to overwrite existing file: {target}')
    if sha256_file(source) != expected_sha:
        raise InstallError(f'{source.name} changed since install; not moving it.')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:
        _copy_verify_unlink(source, target, expected_sha)


def _undo_moves(done: MovePlan) -> None:
    for source, target, sha in reversed(done):
        try:
            _move_file(target, source, sha)
        except (InstallError, OSError) as e:
            ui.warn(f'Could not move {escape(str(target))} back: {escape(str(e))}')


def _move_all(plan: MovePlan) -> None:
    done: MovePlan = []
    try:
        for step in plan:
            _move_file(*step)
            done.append(step)
    except (InstallError, OSError) as e:
        _undo_moves(done)
        raise InstallError(f'{e} (no files were changed)') from e


def _commit(updated: InstalledMod, plan: MovePlan) -> InstalledMod:
    try:
        manifest.replace_mod(updated)
    except ConfigError as e:
        _undo_moves(plan)
        raise InstallError(f'Could not save the new state; files were moved back: {e}') from e
    return updated


# ----------------------------------------------------------------------
# Enable / disable
# ----------------------------------------------------------------------

def _disable_files(current: InstalledMod) -> InstalledMod:
    plan = [(current.addons / f.slot, current.parking / f.slot, f.sha256) for f in current.files]
    _move_all(plan)
    return _commit(replace(current, enabled=False), plan)


def _target_slots(mod: InstalledMod) -> list[str]:
    """Previous slot names where still free, otherwise the lowest free slots."""
    used = used_slots(mod.addons)
    chosen: list[str | None] = []
    for item in mod.files:
        match = SLOT_RE.match(item.slot)
        number = int(match.group(1)) if match else None
        if number is not None and number not in used:
            used.add(number)
            chosen.append(item.slot)
        else:
            chosen.append(None)
    missing = chosen.count(None)
    fresh = iter(free_slot_names(used, missing) if missing else [])
    return [slot or next(fresh) for slot in chosen]


def _enable_files(current: InstalledMod) -> InstalledMod:
    slots = _target_slots(current)
    try:
        current.addons.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InstallError(f'Cannot create {current.addons}: {e}') from e
    plan = [(current.parking / f.slot, current.addons / slot, f.sha256) for f, slot in zip(current.files, slots)]
    _move_all(plan)
    files = tuple(replace(f, slot=slot) for f, slot in zip(current.files, slots))
    updated = _commit(replace(current, enabled=True, files=files), plan)
    remove_empty_dir(current.parking)
    return updated


def enabled_rivals(mod: InstalledMod) -> list[InstalledMod]:
    """Other enabled members of the mod's group."""
    if not mod.group:
        return []
    members = manifest.group_members(manifest.load_mods(), mod.group)
    return [m for m in members if m.enabled and m.mod_id != mod.mod_id]


def _restore(mods) -> None:
    for other in mods:
        try:
            _enable_files(_fresh(other))
        except InstallError as e:
            ui.warn(f'Could not re-enable {escape(other.name)}: {escape(str(e))}')


def _disable_rivals(current: InstalledMod) -> tuple[InstalledMod, ...]:
    done: list[InstalledMod] = []
    try:
        for rival in enabled_rivals(current):
            done.append(_disable_files(_fresh(rival)))
    except InstallError:
        _restore(done)
        raise
    return tuple(done)


def disable(mod: InstalledMod) -> InstalledMod:
    current = _fresh(mod)
    return _disable_files(current) if current.enabled else current


def enable(mod: InstalledMod) -> ToggleResult:
    """Make the mod the active one: enable it (if needed) and disable its group rivals."""
    current = _fresh(mod)
    disabled = _disable_rivals(current)
    if current.enabled:
        return ToggleResult(current, disabled)
    try:
        updated = _enable_files(current)
    except InstallError:
        _restore(disabled)
        raise
    return ToggleResult(updated, disabled)


def disable_group(group: str) -> list[InstalledMod]:
    members = manifest.group_members(manifest.load_mods(), group)
    return [disable(m) for m in members if m.enabled]


# ----------------------------------------------------------------------
# Groups
# ----------------------------------------------------------------------

def set_group(mod: InstalledMod, name: str | None) -> InstalledMod:
    """Assign (or clear with None) a group. Exclusivity is resolved by the caller."""
    current = _fresh(mod)
    group = manifest.canonical_group(manifest.load_mods(), name)
    updated = replace(current, group=group)
    manifest.replace_mod(updated)
    return updated


def _words(text: str) -> set[str]:
    found = {m.group(0).casefold() for m in WORD_RE.finditer(text or '')}
    return {w for w in found if len(w) >= MIN_WORD_LEN and w not in STOP_WORDS}


def _match_score(group: str, words: set[str], flat: str) -> int:
    score = 0
    for word in _words(group):
        if word in words or (len(word) >= MIN_SUBSTRING_LEN and word in flat):
            score += 1
    return score


def suggest_groups(mods: list[InstalledMod], *names: str) -> list[tuple[str, bool]]:
    """All existing groups as (group, matched), best matches for the given names first."""
    text = ' '.join(n for n in names if isinstance(n, str))
    words, flat = _words(text), text.casefold()
    scored = [(_match_score(g, words, flat), g) for g in manifest.list_groups(mods)]
    scored.sort(key=lambda item: (-item[0], manifest.group_key(item[1])))
    return [(group, score > 0) for score, group in scored]
