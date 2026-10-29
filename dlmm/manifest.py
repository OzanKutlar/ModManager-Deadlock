"""Records which files each installed mod placed in the game, plus its enabled state and group."""

import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.markup import escape

from . import heroes, ui
from .backups import backup_file
from .config import app_dir, read_json, write_json
from .errors import ConfigError, InstallError
from .fsutil import format_age

MANIFEST_FILENAME = 'installed.json'
DISABLED_DIRNAME = 'dlmm_disabled'
SCHEMA_VERSION = 3
MAX_MODS = 1_000
MAX_HERO_LEN = 60


@dataclass(frozen=True)
class InstalledFile:
    slot: str
    original: str
    sha256: str
    size: int
    part_of: str | None = None        # chunk files: slot of their _dir.vpk
    healed_from: str | None = None    # Violet fix: hash of the original file
    heal_backup: str | None = None    # Violet fix: backup id holding the original


def parking_dir(addons_dir: str | Path, mod_id: str) -> Path:
    """Where a disabled mod's files live: citadel/dlmm_disabled/<mod_id>."""
    return Path(addons_dir).parent / DISABLED_DIRNAME / mod_id


@dataclass(frozen=True)
class InstalledMod:
    mod_id: str
    name: str
    source: str
    addons_dir: str
    installed: float
    files: tuple[InstalledFile, ...]
    gamebanana_mod: int | None = None
    gamebanana_file: int | None = None
    enabled: bool = True
    hero: str | None = None
    hero_manual: bool = False
    exclusive: bool = True
    packed_from: tuple[InstalledFile, ...] = ()
    pack_backups: tuple[str, ...] = ()

    @property
    def addons(self) -> Path:
        return Path(self.addons_dir)

    @property
    def parking(self) -> Path:
        return parking_dir(self.addons_dir, self.mod_id)

    @property
    def slots(self) -> str:
        names = [f.slot for f in self.files if not f.part_of]
        chunks = len(self.files) - len(names)
        extra = f' (+{chunks} chunk files)' if chunks else ''
        return ', '.join(names) + extra

    @property
    def mark(self) -> str:
        return '✔' if self.enabled else '·'

    @property
    def hint(self) -> str:
        parts = [self.slots if self.enabled else 'disabled']
        if self.hero:
            shared = heroes.is_hero(self.hero) and not self.exclusive
            parts.append(f'{self.hero} (shared)' if shared else self.hero)
        if any(f.healed_from for f in self.files):
            parts.append('violet-fixed')
        if self.packed_from:
            parts.append('packed')
        parts.append(f'installed {format_age(self.installed)}')
        return ' · '.join(parts)


def file_location(mod: InstalledMod, item: InstalledFile) -> Path:
    return (mod.addons if mod.enabled else mod.parking) / item.slot


# ----------------------------------------------------------------------
# Characters
# ----------------------------------------------------------------------

def normalize_hero(name) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str):
        raise InstallError('Character name must be text.')
    clean = ' '.join(name.split())[:MAX_HERO_LEN].strip()
    return clean or None


def hero_key(name: str) -> str:
    return name.casefold()


def list_heroes(mods: list[InstalledMod]) -> list[str]:
    """Real characters that installed mods are for (type labels excluded)."""
    seen: dict[str, str] = {}
    for mod in mods:
        if heroes.is_hero(mod.hero):
            seen.setdefault(hero_key(mod.hero), mod.hero)
    return sorted(seen.values(), key=hero_key)


def canonical_hero(mods: list[InstalledMod], name) -> str | None:
    """Known display name for a typed name or codename, else an existing spelling, else the text."""
    clean = normalize_hero(name)
    if clean is None:
        return None
    known = heroes.canonical(clean)
    if known:
        return known
    for existing in list_heroes(mods):
        if hero_key(existing) == hero_key(clean):
            return existing
    return clean


def hero_members(mods: list[InstalledMod], hero: str | None) -> list[InstalledMod]:
    if not heroes.is_hero(hero):
        return []
    key = hero_key(hero)
    return [m for m in mods if heroes.is_hero(m.hero) and hero_key(m.hero) == key]


def is_exclusive(mod: InstalledMod) -> bool:
    """Whether the one-mod-per-character rule applies to this mod."""
    return mod.exclusive and heroes.is_hero(mod.hero)


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

def new_mod_id() -> str:
    return secrets.token_hex(6)


def _manifest_path() -> Path:
    return app_dir() / MANIFEST_FILENAME


def _optional_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _flag(data: dict, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f'{key} must be true or false')
    return value


def _file_from(data: dict) -> InstalledFile:
    return InstalledFile(
        slot=str(data['slot']), original=str(data['original']),
        sha256=str(data['sha256']), size=int(data['size']),
        part_of=_optional_str(data.get('part_of')),
        healed_from=_optional_str(data.get('healed_from')),
        heal_backup=_optional_str(data.get('heal_backup')),
    )


def _mod_from(data: dict) -> InstalledMod:
    files = tuple(_file_from(item) for item in data['files'])
    if not files:
        raise ValueError('entry lists no files')
    packed_from = tuple(_file_from(item) for item in data.get('packed_from') or ())
    pack_backups = tuple(str(b) for b in data.get('pack_backups') or ())
    if len(pack_backups) != len(packed_from):
        raise ValueError('pack backups do not match the packed parts')
    hero = data.get('hero')
    if hero is not None and not isinstance(hero, str):
        raise ValueError('hero must be text')
    return InstalledMod(
        mod_id=str(data['mod_id']),
        name=str(data['name']),
        source=str(data.get('source', '')),
        addons_dir=str(data['addons_dir']),
        installed=float(data['installed']),
        files=files,
        gamebanana_mod=_optional_int(data.get('gamebanana_mod')),
        gamebanana_file=_optional_int(data.get('gamebanana_file')),
        enabled=_flag(data, 'enabled', True),
        hero=normalize_hero(hero),
        hero_manual=_flag(data, 'hero_manual', False),
        exclusive=_flag(data, 'exclusive', True),
        packed_from=packed_from,
        pack_backups=pack_backups,
    )


def _save(mods: list[InstalledMod]) -> None:
    write_json(_manifest_path(), {'version': SCHEMA_VERSION, 'mods': [asdict(m) for m in mods]})


def _quarantine(path: Path, reason: str, keep: list[InstalledMod]) -> None:
    backup_file(path, f'damaged manifest ({reason})')
    ui.warn(f'Manifest problem ({escape(reason)}); the old file was backed up and cleaned.')
    _save(keep)


def _read_raw_mods(path: Path) -> tuple[list, str | None]:
    try:
        data = read_json(path, {'version': SCHEMA_VERSION, 'mods': []})
    except ConfigError as e:
        return [], str(e)
    mods = data.get('mods') if isinstance(data, dict) else None
    if not isinstance(mods, list):
        return [], 'mods list missing'
    return mods[:MAX_MODS], None


def load_mods() -> list[InstalledMod]:
    path = _manifest_path()
    raw_mods, problem = _read_raw_mods(path)
    mods: list[InstalledMod] = []
    for raw in raw_mods:
        try:
            mods.append(_mod_from(raw))
        except (KeyError, TypeError, ValueError) as e:
            problem = problem or f'malformed entry: {e}'
    if problem:
        _quarantine(path, problem, mods)
    return mods


def find_mod(mod_id: str) -> InstalledMod | None:
    return next((m for m in load_mods() if m.mod_id == mod_id), None)


def add_mod(mod: InstalledMod) -> None:
    if not isinstance(mod, InstalledMod):
        raise InstallError('Invalid mod record.')
    mods = load_mods()
    if any(m.mod_id == mod.mod_id for m in mods):
        raise InstallError(f'Duplicate mod id {mod.mod_id}.')
    _save([*mods, mod])


def save_mods(mods: list[InstalledMod]) -> None:
    """Replace the whole manifest in one atomic write."""
    if not all(isinstance(m, InstalledMod) for m in mods):
        raise InstallError('Invalid mod record.')
    _save(list(mods))


def remove_mod(mod_id: str) -> None:
    _save([m for m in load_mods() if m.mod_id != mod_id])


def replace_mod(mod: InstalledMod) -> None:
    if not isinstance(mod, InstalledMod):
        raise InstallError('Invalid mod record.')
    _save([mod if m.mod_id == mod.mod_id else m for m in load_mods()])
