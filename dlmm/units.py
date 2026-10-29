"""Split VPKs: a pakNN_dir.vpk plus pakNN_000.vpk, pakNN_001.vpk... chunks that must share its number."""

import re
from dataclasses import dataclass, replace

from . import vpk

DIR_SUFFIX = '_dir.vpk'
CHUNK_RE = re.compile(r'^(?P<prefix>.+)_(?P<index>\d{3})\.vpk$', re.IGNORECASE)
DIR_RE = re.compile(r'^(?P<prefix>.+)_dir\.vpk$', re.IGNORECASE)


@dataclass(frozen=True)
class Unit:
    """One loadable VPK: its directory file plus (index, file) chunk pairs."""
    main: object
    chunks: tuple = ()

    @property
    def files(self) -> tuple:
        return (self.main, *(chunk for _index, chunk in self.chunks))


def chunk_index(name: str) -> str | None:
    match = CHUNK_RE.match(name)
    return match.group('index') if match else None


def chunk_slot(dir_slot: str, index: str) -> str:
    """pak05_dir.vpk + '000' -> pak05_000.vpk"""
    if not dir_slot.lower().endswith(DIR_SUFFIX):
        raise ValueError(f'Not a _dir.vpk name: {dir_slot}')
    return f'{dir_slot[:-len(DIR_SUFFIX)]}_{index}.vpk'


def group_key(relative: str) -> tuple[str, str] | None:
    """(folder, prefix) shared by a _dir.vpk and its chunks; None for other names."""
    folder, _sep, name = relative.rpartition('/')
    match = DIR_RE.match(name) or CHUNK_RE.match(name)
    return (folder.lower(), match.group('prefix').lower()) if match else None


def _name(item) -> str:
    return item.relative.rsplit('/', 1)[-1]


def _is_chunk(item) -> bool:
    """Named like a chunk and without a VPK header (directory files always have one)."""
    return chunk_index(_name(item)) is not None and not vpk.has_signature(item.path)


def group_units(files) -> tuple[list[Unit], list]:
    """Attach extracted chunk files to their _dir.vpk. Returns (units, orphan chunks)."""
    items = list(files)
    chunk_paths = {item.path for item in items if _is_chunk(item)}
    mains = [item for item in items if item.path not in chunk_paths]
    owners = {}
    for item in mains:
        key = group_key(item.relative)
        if key is not None and DIR_RE.match(_name(item)):
            owners[key] = item
    attached: dict = {}
    orphans = []
    for item in items:
        if item.path not in chunk_paths:
            continue
        owner = owners.get(group_key(item.relative))
        if owner is None:
            orphans.append(item)
        else:
            attached.setdefault(owner.path, []).append((chunk_index(_name(item)), item))
    units = [Unit(m, tuple(sorted(attached.get(m.path, []), key=lambda c: c[0]))) for m in mains]
    return units, orphans


def mains(files) -> list:
    """Installed files that are not chunks of another file."""
    return [item for item in files if not getattr(item, 'part_of', None)]


def renamed(files, mapping: dict) -> tuple:
    """New names for installed files: mains from `mapping` (old -> new), chunks follow their main."""
    result = []
    for item in files:
        if item.part_of:
            main = mapping[item.part_of]
            result.append(replace(item, slot=chunk_slot(main, chunk_index(item.slot)), part_of=main))
        else:
            result.append(replace(item, slot=mapping[item.slot]))
    return tuple(result)
