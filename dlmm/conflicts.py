"""Detect enabled mods that replace the same game files, by reading their VPK directory trees."""

from dataclasses import dataclass

from . import vpk
from .errors import VpkError
from .manifest import InstalledMod, file_location

MAX_SAMPLE = 5
MAX_MODS = 1_000
IGNORED_PREFIXES = ('readme', 'license', 'licence', 'credits', 'changelog')
COMPILED_SUFFIX = '_c'


@dataclass(frozen=True)
class Conflict:
    first: InstalledMod
    second: InstalledMod
    count: int
    sample: tuple[str, ...]


@dataclass(frozen=True)
class ConflictReport:
    conflicts: tuple[Conflict, ...]
    unreadable: tuple[str, ...]


def is_ignorable(path: str) -> bool:
    """Docs and stray root files that many mods ship; they never matter in-game."""
    name = path.rsplit('/', 1)[-1]
    if name.startswith(IGNORED_PREFIXES):
        return True
    return '/' not in path and not name.endswith(COMPILED_SUFFIX)


def pair_key(a: InstalledMod, b: InstalledMod) -> tuple[str, str]:
    first, second = sorted((a.mod_id, b.mod_id))
    return first, second


def mod_paths(mod: InstalledMod) -> tuple[frozenset[str], list[str]]:
    """Union of all paths in the mod's VPKs, plus a note for each unreadable file."""
    paths: set[str] = set()
    problems: list[str] = []
    for item in mod.files:
        if item.part_of:
            continue  # chunk files carry data only; their _dir.vpk lists the paths
        try:
            paths |= {p for p in vpk.list_files(file_location(mod, item)) if not is_ignorable(p)}
        except VpkError as e:
            problems.append(f'{mod.name} / {item.slot}: {e}')
    return frozenset(paths), problems


def _pair(a: InstalledMod, a_paths: frozenset, b: InstalledMod, b_paths: frozenset) -> Conflict | None:
    shared = a_paths & b_paths
    if not shared:
        return None
    return Conflict(a, b, len(shared), tuple(sorted(shared)[:MAX_SAMPLE]))


def find_conflicts(mods: list[InstalledMod], only_with: InstalledMod | None = None,
                   ignored: set[tuple[str, str]] | None = None) -> ConflictReport:
    """Pairs of enabled mods sharing files (optionally only pairs involving `only_with`)."""
    indexed: list[tuple[InstalledMod, frozenset]] = []
    unreadable: list[str] = []
    for mod in [m for m in mods if m.enabled][:MAX_MODS]:
        paths, problems = mod_paths(mod)
        indexed.append((mod, paths))
        unreadable.extend(problems)
    found: list[Conflict] = []
    for index, (a, a_paths) in enumerate(indexed):
        for b, b_paths in indexed[index + 1:]:
            if only_with is not None and only_with.mod_id not in (a.mod_id, b.mod_id):
                continue
            if ignored and pair_key(a, b) in ignored:
                continue
            conflict = _pair(a, a_paths, b, b_paths)
            if conflict is not None:
                found.append(conflict)
    return ConflictReport(tuple(found), tuple(unreadable))


def find_duplicates(mods: list[InstalledMod]) -> list[tuple[InstalledMod, InstalledMod]]:
    """(older, newer) pairs whose VPK hashes are identical (the same files installed twice)."""
    seen: dict[frozenset, InstalledMod] = {}
    pairs: list[tuple[InstalledMod, InstalledMod]] = []
    for mod in sorted(mods, key=lambda m: m.installed)[:MAX_MODS]:
        key = frozenset(f.sha256 for f in mod.files)
        if key in seen:
            pairs.append((seen[key], mod))
        else:
            seen[key] = mod
    return pairs
