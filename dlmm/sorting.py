"""Shared sort orders for mod and archive pickers, remembered between runs in config.json."""

from rich.markup import escape

from . import heroes, ui
from .config import get_setting, set_setting
from .errors import DlmmError
from .manifest import InstalledMod
from .picker import PickItem, SortChoice, SortOption, pick

MOD_SETTING = 'mod_sort'
ARCHIVE_SETTING = 'archive_sort'
MERGE_SETTING = 'merge_sort'


def character_key(mod: InstalledMod) -> tuple:
    """By character A-Z, mods without a character last, then by name."""
    hero = mod.hero if heroes.is_hero(mod.hero) else ''
    return (not hero, hero.casefold(), mod.name.casefold())


def time_key(mod: InstalledMod) -> tuple:
    """Newest download (install) first."""
    return (-mod.installed, mod.name.casefold())


def name_key(mod: InstalledMod) -> tuple:
    return (mod.name.casefold(),)


MOD_KEYS = {'character': character_key, 'time': time_key, 'name': name_key}

BY_CHARACTER = SortOption('Character A→Z', lambda item: character_key(item.value))
BY_TIME = SortOption('Newest first', lambda item: time_key(item.value))
BY_NAME = SortOption('Name A→Z', lambda item: name_key(item.value))
BY_SIMILARITY = SortOption('Similar names first')
MOD_SORTS = (BY_CHARACTER, BY_TIME, BY_NAME)
ARCHIVE_SORTS = (
    SortOption('Newest first', lambda item: (-item.value.downloaded, item.value.name.casefold())),
    SortOption('Name A→Z', lambda item: (item.value.name.casefold(),)),
)
KINDS = {
    'mods': (MOD_SORTS, MOD_SETTING),
    'archives': (ARCHIVE_SORTS, ARCHIVE_SETTING),
    'merge': ((BY_SIMILARITY, *MOD_SORTS), MERGE_SETTING),
}


def sort_mods(mods: list[InstalledMod], order: str) -> list[InstalledMod]:
    key = MOD_KEYS.get(order)
    if key is None:
        raise ValueError(f'Unknown sort order: {order}')
    return sorted(mods, key=key)


def load_choice(setting: str) -> SortChoice | None:
    """The saved sort for a screen kind; None when missing or malformed."""
    try:
        raw = get_setting(setting)
    except DlmmError as e:
        ui.warn(f'Could not read the saved sort order: {escape(str(e))}')
        return None
    if not isinstance(raw, dict):
        return None
    label, reverse = raw.get('label'), raw.get('reverse', False)
    if not isinstance(label, str) or not isinstance(reverse, bool):
        return None
    return SortChoice(label, reverse)


def save_choice(setting: str, choice: SortChoice | None) -> None:
    if choice is None:
        return
    try:
        set_setting(setting, {'label': choice.label, 'reverse': choice.reverse})
    except DlmmError as e:
        ui.warn(f'Could not save the sort order: {escape(str(e))}')


def pick_sorted(title: str, items: list[PickItem], kind: str = 'mods', default_index: int = 0) -> PickItem | None:
    """pick() with the sorts of `kind` ('mods', 'archives' or 'merge'), remembering the choice."""
    if kind not in KINDS:
        raise ValueError(f'Unknown picker kind: {kind}')
    sorts, setting = KINDS[kind]
    return pick(title, items, default_index, sorts=sorts, sort_choice=load_choice(setting),
                on_sort=lambda choice: save_choice(setting, choice))
