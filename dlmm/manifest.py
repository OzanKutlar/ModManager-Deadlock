"""Records which files each installed mod placed in the game, plus its enabled state and group."""

import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.markup import escape

from . import ui
from .backups import backup_file
from .config import app_dir, read_json, write_json
from .errors import ConfigError, InstallError
from .fsutil import format_age

MANIFEST_FILENAME = 'installed.json'
DISABLED_DIRNAME = 'dlmm_disabled'
SCHEMA_VERSION = 2
MAX_MODS = 1_000
MAX_GROUP_LEN = 60


@dataclass(frozen=True)
class InstalledFile:
    slot: str
    original: str
    sha256: str
    size: int


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
    group: str | None = None

    @property
    def addons(self) -> Path:
        return Path(self.addons_dir)

    @property
    def parking(self) -> Path:
        return parking_dir(self.addons_dir, self.mod_id)

    @property
    def slots(self) -> str:
        return ', '.join(f.slot for f in self.files)

    @property
    def mark(self) -> str:
        return '✔' if self.enabled else '·'

    @property
    def hint(self) -> str:
        parts = [self.slots if self.enabled else 'disabled']
        if self.group:
            parts.append(self.group)
        parts.append(f'installed {format_age(self.installed)}')
        return ' · '.join(parts)


def file_location(mod: InstalledMod, item: InstalledFile) -> Path:
    return (mod.addons if mod.enabled else mod.parking) / item.slot


# ----------------------------------------------------------------------
# Groups
# ----------------------------------------------------------------------

def normalize_group(name) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str):
        raise InstallError('Group name must be text.')
    clean = ' '.join(name.split())[:MAX_GROUP_LEN].strip()
    return clean or None


def group_key(name: str) -> str:
    return name.casefold()


def list_groups(mods: list[InstalledMod]) -> list[str]:
    seen: dict[str, str] = {}
    for mod in mods:
        if mod.group:
            seen.setdefault(group_key(mod.group), mod.group)
    return sorted(seen.values(), key=group_key)


def canonical_group(mods: list[InstalledMod], name) -> str | None:
    """Clean the name and reuse the spelling of an existing group that matches it."""
    clean = normalize_group(name)
    if clean is None:
        return None
    for existing in list_groups(mods):
        if group_key(existing) == group_key(clean):
            return existing
    return clean


def group_members(mods: list[InstalledMod], group: str | None) -> list[InstalledMod]:
    if not group:
        return []
    key = group_key(group)
    return [m for m in mods if m.group and group_key(m.group) == key]


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

def new_mod_id() -> str:
    return secrets.token_hex(6)


def _manifest_path() -> Path:
    return app_dir() / MANIFEST_FILENAME


def _optional_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _file_from(data: dict) -> InstalledFile:
    return InstalledFile(
        slot=str(data['slot']), original=str(data['original']),
        sha256=str(data['sha256']), size=int(data['size']),
    )


def _mod_from(data: dict) -> InstalledMod:
    files = tuple(_file_from(item) for item in data['files'])
    if not files:
        raise ValueError('entry lists no files')
    enabled = data.get('enabled', True)
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be true or false')
    group = data.get('group')
    if group is not None and not isinstance(group, str):
        raise ValueError('group must be text')
    return InstalledMod(
        mod_id=str(data['mod_id']),
        name=str(data['name']),
        source=str(data.get('source', '')),
        addons_dir=str(data['addons_dir']),
        installed=float(data['installed']),
        files=files,
        gamebanana_mod=_optional_int(data.get('gamebanana_mod')),
        gamebanana_file=_optional_int(data.get('gamebanana_file')),
        enabled=enabled,
        group=normalize_group(group),
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


def remove_mod(mod_id: str) -> None:
    _save([m for m in load_mods() if m.mod_id != mod_id])


def replace_mod(mod: InstalledMod) -> None:
    if not isinstance(mod, InstalledMod):
        raise InstallError('Invalid mod record.')
    _save([mod if m.mod_id == mod.mod_id else m for m in load_mods()])
