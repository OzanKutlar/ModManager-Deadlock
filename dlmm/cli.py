"""Command-line entry point: argparse subcommands plus an interactive main menu."""

import argparse
import os
import sys
from pathlib import Path

from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

from . import __version__, archives, backups, gameinfo, installer, manifest, protocol
from .archives import ExtractedVpk
from .errors import DlmmError
from .fsutil import format_age, format_size
from .game_paths import find_game
from .gamebanana import RemoteFile, download, fetch_remote_file, parse_mod_link
from .installer import InstallRequest
from .manifest import InstalledMod
from .picker import PickItem, confirm, pause, pick
from .ui import console, dim, fail, info, ok, rule, warn

MAX_MENU_ROUNDS = 200
DOWNLOADS_GUID = '{374DE290-123F-4565-9164-39C4925E467B}'
SHELL_FOLDERS_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
MENU_ACTIONS = (
    ('Install mod from Downloads', 'install'),
    ('Uninstall mod', 'uninstall'),
    ('List installed mods', 'list'),
    ('Roll back a backup', 'rollback'),
    ('Re-apply gameinfo.gi patch', 'patch'),
    ('Status', 'status'),
)
SIMPLE_COMMANDS = (
    ('list', 'List installed mods'),
    ('status', 'Show game path, patch state, tools and backups'),
    ('rollback', 'Restore a backed-up file'),
    ('patch', 'Make gameinfo.gi load citadel/addons (after game updates)'),
    ('unpatch', 'Remove the addons entry from gameinfo.gi'),
    ('register', 'Handle GameBanana 1-click links with dlmm (Windows)'),
    ('unregister', 'Restore the previous 1-click handler (Windows)'),
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def default_downloads() -> Path:
    fallback = Path.home() / 'Downloads'
    if sys.platform != 'win32':
        return fallback
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, DOWNLOADS_GUID)
    except OSError:
        return fallback  # no custom Downloads location configured
    return Path(os.path.expandvars(value)) if isinstance(value, str) and value else fallback


def choose_vpks(vpks: list[ExtractedVpk]) -> list[ExtractedVpk]:
    items = [PickItem(f'Install all {len(vpks)} files', 'for mods split into several parts', list(vpks))]
    items.extend(PickItem(vpk.relative, format_size(vpk.size), [vpk]) for vpk in vpks)
    choice = pick('This archive has several .vpk files (usually variants) - pick one', items, default_index=1)
    return list(choice.value) if choice else []


def _run_install(request: InstallRequest, args) -> int:
    game = find_game(args.game_dir)
    rule(f'Installing {escape(request.name)}')
    record = installer.install(request, game, choose_vpks)
    if record is None:
        warn('Nothing selected; install cancelled.')
        return 0
    ok(f'Installed [bold]{escape(record.name)}[/bold] as {escape(record.slots)}')
    dim('Restart Deadlock if it is running.')
    return 0


def _pick_archive(folder: Path) -> Path | None:
    found = archives.scan_downloads(folder)
    if not found:
        fail(f'No mod archives (.rar/.zip/.7z/.vpk) in {escape(str(folder))}.')
        return None
    installed = {m.source for m in manifest.load_mods()}
    items = []
    for item in found:
        marker = ' · installed' if str(item.path.resolve()) in installed else ''
        items.append(PickItem(item.name, item.hint + marker, item.path))
    choice = pick(f'Select mod archive ({folder})', items)
    return choice.value if choice else None


def _pick_mod(mods: list[InstalledMod]) -> InstalledMod | None:
    ordered = sorted(mods, key=lambda m: -m.installed)
    choice = pick('Select mod to uninstall', [PickItem(m.name, m.hint, m) for m in ordered])
    return choice.value if choice else None


def _match_mod(mods: list[InstalledMod], query: str) -> InstalledMod | None:
    lowered = query.strip().lower()
    exact = [m for m in mods if m.name.lower() == lowered]
    if len(exact) == 1:
        return exact[0]
    partial = [m for m in mods if lowered in m.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        fail(f'No installed mod matches {escape(query)}.')
        return None
    return _pick_mod(partial)


def _offer_unpatch(args) -> None:
    if manifest.load_mods():
        return
    try:
        game = find_game(args.game_dir)
    except DlmmError as e:
        warn(escape(str(e)))
        return
    if installer.unmanaged_vpks(game.addons, []):
        return
    if confirm('No mods left. Remove the addons entry from gameinfo.gi too?'):
        if gameinfo.remove_patch(game.gameinfo):
            ok('Removed the addons entry (backup saved).')


def _backup_hint(entry: backups.BackupEntry) -> str:
    pinned = ' · original' if entry.pinned else ''
    return f'{format_size(entry.size)} · {format_age(entry.created)}{pinned}'


def _download_with_progress(remote: RemoteFile, folder: Path) -> Path:
    columns = ('[progress.description]{task.description}', BarColumn(), DownloadColumn(),
               TransferSpeedColumn(), TimeRemainingColumn())
    with Progress(*columns, console=console) as progress:
        task = progress.add_task('Downloading', total=remote.size or None)

        def _update(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total or None)

        return download(remote, folder, _update)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------

def cmd_install(args) -> int:
    archive = Path(args.archive).expanduser() if args.archive else _pick_archive(Path(args.downloads))
    if archive is None:
        warn('No archive selected.')
        return 0
    return _run_install(InstallRequest(archive, args.name or archive.stem), args)


def cmd_uninstall(args) -> int:
    mods = manifest.load_mods()
    if not mods:
        info('No mods installed by dlmm.')
        return 0
    target = _match_mod(mods, args.name) if args.name else _pick_mod(mods)
    if target is None:
        return 0
    if not confirm(f'Uninstall {target.name} ({target.slots})?'):
        warn('Uninstall cancelled.')
        return 0
    result = installer.uninstall(target)
    for slot in result.removed:
        ok(f'Removed {escape(slot)} (backup kept, see rollback)')
    for note in result.skipped:
        warn(f'Skipped {escape(note)}')
    _offer_unpatch(args)
    return 0


def cmd_list(args) -> int:
    mods = manifest.load_mods()
    if not mods:
        info('No mods installed by dlmm.')
        return 0
    rule('Installed mods')
    for mod in sorted(mods, key=lambda m: m.files[0].slot):
        age = escape(format_age(mod.installed))
        console.print(f'  [cyan]{escape(mod.slots)}[/cyan]  {escape(mod.name)}  [dim]{age}[/dim]')
    return 0


def _status_game(args) -> None:
    try:
        game = find_game(args.game_dir)
    except DlmmError as e:
        fail(escape(str(e)))
        return
    ok(f'Game folder: {escape(str(game.citadel))}')
    try:
        patched = gameinfo.is_patched(gameinfo.read_gameinfo(game.gameinfo))
    except DlmmError as e:
        fail(f'gameinfo.gi: {escape(str(e))}')
    else:
        if patched:
            ok('gameinfo.gi: loads citadel/addons')
        else:
            warn('gameinfo.gi: not patched (run: patch)')
    mods = manifest.load_mods()
    info(f'Managed mods: {len(mods)}')
    unmanaged = installer.unmanaged_vpks(game.addons, mods)
    if unmanaged:
        joined = ', '.join(unmanaged)
        warn(f'VPKs in addons not managed by dlmm: {escape(joined)}')


def _status_tools() -> None:
    tool = archives.find_rar_tool()
    if tool:
        ok(f'RAR extractor: {escape(tool.path)}')
    else:
        warn('No RAR extractor found (install 7-Zip or WinRAR).')
    if archives.has_py7zr():
        ok('7z support: py7zr installed')
    else:
        warn('7z support: py7zr missing (pip install py7zr)')


def _status_backups() -> None:
    entries = backups.list_backups()
    total = sum(e.size for e in entries)
    root = escape(str(backups.backups_root()))
    info(f'Backups: {len(entries)} ({format_size(total)}) in {root}')


def cmd_status(args) -> int:
    rule(f'dlmm {__version__} status')
    _status_game(args)
    _status_tools()
    info(f'1-click handler: {escape(protocol.status())}')
    _status_backups()
    info(f'Downloads folder: {escape(str(args.downloads))}')
    return 0


def cmd_rollback(args) -> int:
    entries = backups.list_backups()
    if not entries:
        info('No backups have been made yet.')
        return 0
    items = [PickItem(f'{e.stored_file} · {e.reason}', _backup_hint(e), e) for e in entries]
    choice = pick('Select a backup to restore (newest first)', items)
    if choice is None:
        return 0
    entry = choice.value
    console.print(Panel(
        f'File: [bold]{escape(entry.stored_file)}[/bold]\n'
        f'Restore to: {escape(entry.original_path)}\n'
        f'Reason: {escape(entry.reason)}\n'
        f'Taken: {escape(format_age(entry.created))}',
        title='Roll back', border_style='yellow',
    ))
    if not confirm('Restore this backup? The current file is backed up first.'):
        warn('Rollback cancelled.')
        return 0
    backups.restore_backup(entry)
    ok(f'Restored {escape(entry.original_path)}')
    if entry.stored_file.lower().endswith('.vpk'):
        dim('Restored VPKs are not tracked as mods; status lists them as unmanaged.')
    return 0


def cmd_patch(args) -> int:
    game = find_game(args.game_dir)
    if gameinfo.ensure_patched(game.gameinfo):
        ok('gameinfo.gi patched (backup saved).')
    else:
        info('gameinfo.gi already loads citadel/addons first.')
    return 0


def cmd_unpatch(args) -> int:
    game = find_game(args.game_dir)
    if not confirm('Remove citadel/addons from gameinfo.gi? Mods will stop loading.'):
        return 0
    if gameinfo.remove_patch(game.gameinfo):
        ok('Addons entry removed (backup saved).')
    else:
        info('gameinfo.gi had no addons entry.')
    return 0


def cmd_register(args) -> int:
    previous = protocol.register()
    ok('deadlock-mod-manager: links now open dlmm.')
    if previous:
        dim(f'Previous handler saved; unregister restores it: {escape(previous)}')
    info('Click 1-click install on a GameBanana Deadlock mod to try it.')
    return 0


def cmd_unregister(args) -> int:
    ok(f'Unregister: {escape(protocol.unregister())}')
    return 0


def _handle_link(args) -> int:
    link = parse_mod_link(args.url)
    rule('GameBanana 1-click install')
    info(f'Looking up file {link.file_id} of mod {link.item_id}...')
    remote = fetch_remote_file(link)
    find_game(args.game_dir)
    console.print(Panel(
        f'Mod: [bold green]{escape(remote.mod_name)}[/bold green]\n'
        f'File: {escape(remote.filename)} ({format_size(remote.size)})\n'
        f'Page: https://gamebanana.com/mods/{link.item_id}',
        title='Download & install?', border_style='blue',
    ))
    if not confirm('Download and install this mod?', default=True):
        warn('Cancelled.')
        return 0
    path = _download_with_progress(remote, Path(args.downloads))
    ok(f'Saved to {escape(str(path))}')
    request = InstallRequest(path, remote.mod_name, link.item_id, link.file_id)
    return _run_install(request, args)


def cmd_handle(args) -> int:
    try:
        return _handle_link(args)
    except DlmmError as e:
        fail(escape(str(e)))
        return 1
    finally:
        pause()


COMMANDS = {
    'install': cmd_install,
    'uninstall': cmd_uninstall,
    'list': cmd_list,
    'status': cmd_status,
    'rollback': cmd_rollback,
    'patch': cmd_patch,
    'unpatch': cmd_unpatch,
    'register': cmd_register,
    'unregister': cmd_unregister,
    'handle': cmd_handle,
}


# ----------------------------------------------------------------------
# Interactive menu
# ----------------------------------------------------------------------

def _warn_if_unpatched(args) -> None:
    if not manifest.load_mods():
        return
    try:
        game = find_game(args.game_dir)
        patched = gameinfo.is_patched(gameinfo.read_gameinfo(game.gameinfo))
    except DlmmError as e:
        warn(escape(str(e)))
        return
    if patched:
        return
    warn('gameinfo.gi lost its addons entry (usually a game update) - your mods are inactive.')
    if confirm('Re-apply the patch now?', default=True):
        gameinfo.ensure_patched(game.gameinfo)
        ok('gameinfo.gi patched (backup saved).')


def _run_menu_command(command: str, args) -> None:
    sub_args = argparse.Namespace(game_dir=args.game_dir, downloads=args.downloads, archive=None, name=None)
    try:
        COMMANDS[command](sub_args)
    except DlmmError as e:
        fail(escape(str(e)))


def interactive(args) -> int:
    console.print(Panel(f'Deadlock Lite Mod Manager [dim]v{__version__}[/dim]', border_style='blue'))
    _warn_if_unpatched(args)
    items = [PickItem(label, '', command) for label, command in MENU_ACTIONS]
    items.append(PickItem('Quit', '', None))
    for _ in range(MAX_MENU_ROUNDS):
        choice = pick('What do you want to do?', items)
        if choice is None or choice.value is None:
            return 0
        _run_menu_command(choice.value, args)
    return 0


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='deadlock_mm',
        description='Lightweight Deadlock mod manager. Run without a command for the interactive menu.',
    )
    parser.add_argument('--version', action='version', version=f'dlmm {__version__}')
    parser.add_argument('--game-dir', help='Deadlock install folder (remembered after first use)')
    parser.add_argument('--downloads', type=Path, default=default_downloads(),
                        help='Folder to scan for mod archives (default: your Downloads)')
    sub = parser.add_subparsers(dest='command')
    install = sub.add_parser('install', help='Install a mod archive (picker over Downloads if omitted)')
    install.add_argument('archive', nargs='?', help='Path to a .rar/.zip/.7z/.vpk file')
    install.add_argument('--name', help='Display name for the mod (default: archive name)')
    uninstall = sub.add_parser('uninstall', help='Uninstall a mod installed by dlmm')
    uninstall.add_argument('name', nargs='?', help='Mod name (picker if omitted)')
    handle = sub.add_parser('handle', help='Handle a deadlock-mod-manager: 1-click link')
    handle.add_argument('url')
    for name, text in SIMPLE_COMMANDS:
        sub.add_parser(name, help=text)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command is None:
            return interactive(args)
        return COMMANDS[args.command](args)
    except DlmmError as e:
        fail(escape(str(e)))
        return 1
    except KeyboardInterrupt:
        warn('Interrupted.')
        return 130
