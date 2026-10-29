"""Enable/disable mods by moving their VPKs between addons/ and dlmm_disabled/, and manage characters.

Only one exclusive mod per character may be enabled: enabling one disables the others.
A mod can be allowed to share its character (exclusive=False) to override the rule.
Every move is hash-checked and all-or-nothing per mod (partial moves are rolled back).
"""

import re
from dataclasses import dataclass, replace

from rich.markup import escape

from . import heroes, manifest, ui, units, vpk
from .errors import ConfigError, InstallError, VpkError
from .fileops import MovePlan, move_all, undo_moves
from .installer import relocate, remove_empty_dir, used_slots
from .manifest import InstalledMod

MIN_WORD_LEN = 3
MIN_SUBSTRING_LEN = 4
WORD_RE = re.compile(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+')
STOP_WORDS = frozenset({
    'skin', 'skins', 'mod', 'mods', 'the', 'and', 'for', 'new', 'vpk', 'pak', 'dir', 'alt',
    'version', 'with', 'remake', 'remastered', 'pack', 'zip', 'rar', 'final', 'fix', 'fixed',
    'update', 'updated', 'edition', 'custom', 'replacement', 'replace', 'model', 'models',
})


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
# State changes
# ----------------------------------------------------------------------

def _commit(updated: InstalledMod, plan: MovePlan) -> InstalledMod:
    try:
        manifest.replace_mod(updated)
    except ConfigError as e:
        undo_moves(plan)
        raise InstallError(f'Could not save the new state; files were moved back: {e}') from e
    return updated


# ----------------------------------------------------------------------
# Enable / disable
# ----------------------------------------------------------------------

def _disable_files(current: InstalledMod) -> InstalledMod:
    plan = [(current.addons / f.slot, current.parking / f.slot, f.sha256) for f in current.files]
    move_all(plan)
    return _commit(replace(current, enabled=False), plan)


def _enable_files(current: InstalledMod) -> InstalledMod:
    files = relocate(current.files, used_slots(current.addons))
    try:
        current.addons.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InstallError(f'Cannot create {current.addons}: {e}') from e
    plan = [
        (current.parking / old.slot, current.addons / new.slot, old.sha256)
        for old, new in zip(current.files, files)
    ]
    move_all(plan)
    updated = _commit(replace(current, enabled=True, files=files), plan)
    remove_empty_dir(current.parking)
    return updated


def enabled_rivals(mod: InstalledMod) -> list[InstalledMod]:
    """Other enabled, exclusive mods for the same character (empty when this mod may share)."""
    if not manifest.is_exclusive(mod):
        return []
    members = manifest.hero_members(manifest.load_mods(), mod.hero)
    return [m for m in members if m.enabled and m.exclusive and m.mod_id != mod.mod_id]


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


def fresh(mod: InstalledMod) -> InstalledMod:
    """Latest manifest record of `mod`."""
    return _fresh(mod)


def move_to_state(mod: InstalledMod, enabled: bool) -> InstalledMod:
    """Move a mod's files to match `enabled` without group handling (used by merges)."""
    current = _fresh(mod)
    if current.enabled == enabled:
        return current
    return _enable_files(current) if enabled else _disable_files(current)


def disable_hero(hero: str) -> list[InstalledMod]:
    members = manifest.hero_members(manifest.load_mods(), hero)
    return [disable(m) for m in members if m.enabled]


# ----------------------------------------------------------------------
# Characters
# ----------------------------------------------------------------------

def set_hero(mod: InstalledMod, hero: str | None, manual: bool = True) -> InstalledMod:
    """Set (or clear with None) the character. Exclusivity is resolved by the caller."""
    current = _fresh(mod)
    updated = replace(current, hero=manifest.canonical_hero(manifest.load_mods(), hero), hero_manual=manual)
    manifest.replace_mod(updated)
    return updated


def set_exclusive(mod: InstalledMod, exclusive: bool) -> InstalledMod:
    """exclusive=False lets the mod stay enabled next to other mods for its character."""
    current = _fresh(mod)
    updated = replace(current, exclusive=bool(exclusive))
    manifest.replace_mod(updated)
    return updated


def detect_hero(mod: InstalledMod) -> str | None:
    """Character (or mod type label) from the paths in the mod's VPKs; None if none are readable."""
    paths: list[str] = []
    for item in units.mains(mod.files):
        try:
            paths.extend(vpk.list_files(manifest.file_location(mod, item)))
        except VpkError:
            continue  # unreadable tree: detection is best effort
    return heroes.describe(paths) if paths else None


def refresh_hero(mod: InstalledMod) -> InstalledMod:
    """Re-detect the character and switch the mod back to automatic detection."""
    current = _fresh(mod)
    updated = replace(current, hero=detect_hero(current), hero_manual=False)
    manifest.replace_mod(updated)
    return updated


def backfill_heroes() -> int:
    """Detect the character of mods that have none yet (e.g. installed before detection existed)."""
    mods = manifest.load_mods()
    changed: dict[str, InstalledMod] = {}
    for mod in mods:
        if mod.hero is None and not mod.hero_manual:
            detected = detect_hero(mod)
            if detected:
                changed[mod.mod_id] = replace(mod, hero=detected)
    if changed:
        manifest.save_mods([changed.get(m.mod_id, m) for m in mods])
    return len(changed)


def _words(text: str) -> set[str]:
    found = {m.group(0).casefold() for m in WORD_RE.finditer(text or '')}
    return {w for w in found if len(w) >= MIN_WORD_LEN and w not in STOP_WORDS}


def name_words(text: str) -> set[str]:
    """Meaningful lower-case words of a name (camelCase and separators split, stop words removed)."""
    return _words(text)

