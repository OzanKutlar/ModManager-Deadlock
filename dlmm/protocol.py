"""Register dlmm as the per-user handler for deadlock-mod-manager: links (Windows only).

Any handler that was registered before (e.g. the official manager) is saved in config.json
and restored by unregister().
"""

import sys
import time
from pathlib import Path

from .config import get_setting, set_setting
from .errors import ProtocolError

SCHEME = 'deadlock-mod-manager'
BACKSLASH = chr(92)
KEY_PATH = BACKSLASH.join(('Software', 'Classes', SCHEME))
COMMAND_PATH = BACKSLASH.join((KEY_PATH, 'shell', 'open', 'command'))
DELETE_ORDER = (
    COMMAND_PATH,
    BACKSLASH.join((KEY_PATH, 'shell', 'open')),
    BACKSLASH.join((KEY_PATH, 'shell')),
    BACKSLASH.join((KEY_PATH, 'DefaultIcon')),
    KEY_PATH,
)
ENTRY_SCRIPT = 'deadlock_mm.py'
BACKUP_SETTING = 'protocol_previous_command'


def _winreg():
    if sys.platform != 'win32':
        raise ProtocolError('Protocol registration is only supported on Windows.')
    import winreg
    return winreg


def _python_executable() -> Path:
    """Use python.exe (not pythonw.exe) so the handler gets a console window."""
    exe = Path(sys.executable)
    if exe.name.lower() == 'pythonw.exe':
        console_exe = exe.with_name('python.exe')
        if console_exe.is_file():
            return console_exe
    return exe


def build_command() -> str:
    script = Path(__file__).resolve().parent.parent / ENTRY_SCRIPT
    if not script.is_file():
        raise ProtocolError(f'Entry script not found: {script}')
    return f'"{_python_executable()}" "{script}" handle "%1"'


def read_command() -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, COMMAND_PATH) as key:
            value, _kind = winreg.QueryValueEx(key, '')
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ProtocolError(f'Cannot read registry key: {e}') from e
    return value if isinstance(value, str) else None


def is_ours(command: str | None) -> bool:
    return bool(command) and ENTRY_SCRIPT.lower() in command.lower()


def _write(command: str) -> None:
    winreg = _winreg()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY_PATH) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:Deadlock Mod Manager Protocol')
            winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, COMMAND_PATH) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, command)
    except OSError as e:
        raise ProtocolError(f'Cannot write registry key: {e}') from e


def _delete_keys() -> None:
    winreg = _winreg()
    for path in DELETE_ORDER:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            continue
        except OSError as e:
            raise ProtocolError(f'Cannot delete registry key {path}: {e}') from e


def register() -> str | None:
    """Point the scheme at dlmm. Returns the previous foreign command if one was saved."""
    previous = read_command()
    foreign = previous if previous and not is_ours(previous) else None
    if foreign:
        set_setting(BACKUP_SETTING, {'command': foreign, 'saved': time.time()})
    _write(build_command())
    return foreign


def unregister() -> str:
    """Restore the saved handler, or remove ours. Returns what happened."""
    current = read_command()
    if current is not None and not is_ours(current):
        return 'not registered to dlmm - left untouched'
    backup = get_setting(BACKUP_SETTING)
    command = backup.get('command') if isinstance(backup, dict) else None
    if isinstance(command, str) and command:
        _write(command)
        set_setting(BACKUP_SETTING, None)
        return 'previous handler restored'
    if current is None:
        return 'nothing to unregister'
    _delete_keys()
    return 'handler removed'


def status() -> str:
    if sys.platform != 'win32':
        return 'not supported on this platform'
    command = read_command()
    if command is None:
        return 'not registered'
    return 'registered to dlmm' if is_ours(command) else 'registered to another app'
