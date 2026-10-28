"""App data directory and persistent settings (config.json)."""

import json
import os
import sys
from pathlib import Path

from .errors import ConfigError
from .fsutil import atomic_write_text

APP_NAME = 'dlmm'
CONFIG_FILENAME = 'config.json'
HOME_ENV = 'DLMM_HOME'


def app_dir() -> Path:
    """%APPDATA%/dlmm on Windows, ~/.local/share/dlmm elsewhere (DLMM_HOME overrides)."""
    override = os.environ.get(HOME_ENV)
    if override:
        base = Path(override)
    elif sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / APP_NAME
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / APP_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ConfigError(f'Cannot create data folder {base}: {e}') from e
    return base


def read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError) as e:
        raise ConfigError(f'Could not read {path}: {e}') from e


def write_json(path: Path, data) -> None:
    try:
        atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    except (OSError, TypeError, ValueError) as e:
        raise ConfigError(f'Could not write {path}: {e}') from e


def _config_path() -> Path:
    return app_dir() / CONFIG_FILENAME


def _check_key(key: str) -> None:
    if not isinstance(key, str) or not key:
        raise ConfigError('Setting key must be a non-empty string.')


def load_config() -> dict:
    data = read_json(_config_path(), {})
    return data if isinstance(data, dict) else {}


def get_setting(key: str, default=None):
    _check_key(key)
    return load_config().get(key, default)


def set_setting(key: str, value) -> None:
    """Store a setting; a value of None removes it."""
    _check_key(key)
    config = load_config()
    if value is None:
        config.pop(key, None)
    else:
        config[key] = value
    write_json(_config_path(), config)
