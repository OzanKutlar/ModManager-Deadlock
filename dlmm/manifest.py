"""Records which files each installed mod placed in the addons folder."""

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
SCHEMA_VERSION = 1
MAX_MODS = 1_000


@dataclass(frozen=True)
class InstalledFile:
    slot: str
    original: str
    sha256: str
    size: int


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

    @property
    def slots(self) -> str:
        return ', '.join(f.slot for f in self.files)

    @property
    def hint(self) -> str:
        return f'{self.slots} · installed {format_age(self.installed)}'


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
    return InstalledMod(
        mod_id=str(data['mod_id']),
        name=str(data['name']),
        source=str(data.get('source', '')),
        addons_dir=str(data['addons_dir']),
        installed=float(data['installed']),
        files=files,
        gamebanana_mod=_optional_int(data.get('gamebanana_mod')),
        gamebanana_file=_optional_int(data.get('gamebanana_file')),
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
    _save([mod if m.mod_id == mod.mod_id else m for m in load_mods()])
