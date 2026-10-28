"""Locate Deadlock's game/citadel folder via --game-dir, a saved path, or Steam."""

import itertools
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import get_setting, set_setting
from .errors import GameNotFoundError

DEADLOCK_FOLDER = 'Deadlock'
GAMEINFO_NAME = 'gameinfo.gi'
ADDONS_NAME = 'addons'
STEAM_REG_KEY = r'Software\Valve\Steam'
GAME_DIR_SETTING = 'game_dir'
MAX_LIBRARIES = 64
BACKSLASH = chr(92)
VDF_PATH_RE = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class GamePaths:
    citadel: Path

    @property
    def gameinfo(self) -> Path:
        return self.citadel / GAMEINFO_NAME

    @property
    def addons(self) -> Path:
        return self.citadel / ADDONS_NAME


def _steam_from_registry() -> Path | None:
    if sys.platform != 'win32':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_REG_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, 'SteamPath')
    except OSError:
        return None  # Steam not registered for this user; fall back to default paths
    return Path(value) if isinstance(value, str) and value else None


def _default_steam_roots() -> list[Path]:
    if sys.platform == 'win32':
        program_files = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        return [Path(program_files) / 'Steam']
    home = Path.home()
    return [
        home / '.steam' / 'steam',
        home / '.local' / 'share' / 'Steam',
        home / '.var' / 'app' / 'com.valvesoftware.Steam' / '.local' / 'share' / 'Steam',
    ]


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    for candidate in [_steam_from_registry(), *_default_steam_roots()]:
        if candidate is not None and candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _library_folders(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    vdf = steam_root / 'steamapps' / 'libraryfolders.vdf'
    if not vdf.is_file():
        return libraries
    try:
        text = vdf.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        raise GameNotFoundError(f'Cannot read {vdf}: {e}') from e
    for match in itertools.islice(VDF_PATH_RE.finditer(text), MAX_LIBRARIES):
        libraries.append(Path(match.group(1).replace(BACKSLASH * 2, BACKSLASH)))
    return libraries


def _is_citadel(path: Path) -> bool:
    return path.name.lower() == 'citadel' and (path / GAMEINFO_NAME).is_file()


def normalize_game_dir(raw) -> Path | None:
    """Accepts the Deadlock folder, its game folder, or game/citadel itself."""
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        return None
    base = Path(str(raw).strip().strip('"')).expanduser()
    for candidate in (base, base / 'citadel', base / 'game' / 'citadel'):
        if _is_citadel(candidate):
            return candidate.resolve()
    return None


def _discover_via_steam() -> Path | None:
    for root in _steam_roots():
        for library in _library_folders(root):
            candidate = library / 'steamapps' / 'common' / DEADLOCK_FOLDER / 'game' / 'citadel'
            if _is_citadel(candidate):
                return candidate.resolve()
    return None


def find_game(override: str | None = None) -> GamePaths:
    """Resolve the citadel folder and remember it for next time."""
    saved = get_setting(GAME_DIR_SETTING)
    if override:
        citadel = normalize_game_dir(override)
        if citadel is None:
            raise GameNotFoundError(f'No game/citadel/gameinfo.gi found under {override}.')
    else:
        citadel = normalize_game_dir(saved) or _discover_via_steam()
    if citadel is None:
        raise GameNotFoundError(
            'Deadlock was not found. Pass --game-dir with your Deadlock folder once; it will be remembered.'
        )
    if saved != str(citadel):
        set_setting(GAME_DIR_SETTING, str(citadel))
    return GamePaths(citadel)
