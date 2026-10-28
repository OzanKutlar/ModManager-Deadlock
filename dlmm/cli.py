"""Command-line entry point: argparse subcommands plus an interactive main menu."""

import argparse
import os
import sys
from pathlib import Path

from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

from . import __version__, archives, backups, conflicts, gameinfo, installer, manifest, protocol, toggler
from .archives import ExtractedVpk
from .config import get_setting, set_setting
from .errors import DlmmError
from .fsutil import format_age, format_size
from .game_paths import find_game
from .gamebanana import RemoteFile, download, fetch_remote_file, parse_mod_link
from .installer import InstallRequest
from .manifest import InstalledMod
from .picker import PickItem, confirm, pause, pick
from .ui import console, dim, fail, info, ok, rule, warn

MAX_MENU_ROUNDS = 200
MAX_TEXT_ATTEMPTS = 3
PROTOCOL_PROMPT_SETTING = 'protocol_prompt_answered'
PROTOCOL_PROMPT = (
    "Do you want to let this tool take over deadlock mod manager's url? "
    'so you can download one click from sites like banana'
)
DOWNLOADS_GUID = '{374DE290-123F-4565-9164-39C4925E467B}'
SHELL_FOLDERS_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
NEW_GROUP = object()
NO_GROUP = object()
DISABLE_ALL = object()
MENU_ACTIONS = (
    ('Install mod from Downloads', 'install'),
    ('Enable / disable mods', 'toggle'),
    ('Mod groups', 'group'),
    ('Uninstall mod', 'uninstall'),
    ('List installed mods', 'list'),
    ('Check for conflicts', 'conflicts'),
    ('Roll back a backup', 'rollback'),
    ('Re-apply gameinfo.gi patch', 'patch'),
    ('Status', 'status'),
)
GROUP_ACTIONS = (
    ('Assign a mod to a group', 'assign'),
    ('Switch the active mod in a group', 'switch'),
    ('List groups', 'list'),
)
SIMPLE_COMMANDS = (
    ('list', 'List installed mods by group'),
    ('status', 'Show game path, patch state, tools, conflicts and backups'),
    ('toggle', 'Enable/disable mods from a picker'),
    ('conflicts', 'Find enabled mods that replace the same files'),
    ('rollback', 'Restore a backed-up file'),
    ('patch', 'Make gameinfo.gi load citadel/addons (after game updates)'),
    ('unpatch', 'Remove the addons entry from gameinfo.gi'),
    ('register', 'Handle GameBanana 1-click links with dlmm (Windows)'),
    ('unregister', 'Restore the previous 1-click handler (Windows)'),
)


# ----------------------------------------------------------------------
# General helpers
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


def _ask_text(prompt: str) -> str | None:
    for _ in range(MAX_TEXT_ATTEMPTS):
        try:
            answer = console.input(f'[bold]{escape(prompt)}[/bold] ').strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if answer:
            return answer
        warn('Please type a name (Ctrl+C cancels).')
    return None


def choose_vpks(vpks: list[ExtractedVpk]) -> list[ExtractedVpk]:
    items = [PickItem(f'Install all {len(vpks)} files as one mod', 'for mods split into several parts', list(vpks))]
    items.extend(PickItem(vpk.relative, format_size(vpk.size), [vpk]) for vpk in vpks)
    choice = pick('This archive has several .vpk files (usually variants) - pick one', items, default_index=1)
    return list(choice.value) if choice else []


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


def _mod_items(mods: list[InstalledMod]) -> list[PickItem]:
    return [PickItem(f'{m.mark} {m.name}', m.hint, m) for m in mods]


def _pick_mod(mods: list[InstalledMod], title: str) -> InstalledMod | None:
    ordered = sorted(mods, key=lambda m: m.name.casefold())
    choice = pick(title, _mod_items(ordered))
    return choice.value if choice else None


def _match_mod(mods: list[InstalledMod], query: str, title: str) -> InstalledMod | None:
    lowered = query.strip().casefold()
    exact = [m for m in mods if m.name.casefold() == lowered]
    if len(exact) == 1:
        return exact[0]
    partial = [m for m in mods if lowered in m.name.casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        fail(f'No matching mod for {escape(query)}.')
        return None
    return _pick_mod(partial, title)


def _select_mod(mods: list[InstalledMod], query: str | None, title: str, empty: str) -> InstalledMod | None:
    if not mods:
        info(empty)
        return None
    return _match_mod(mods, query, title) if query else _pick_mod(mods, title)


def _report_toggle(result: toggler.ToggleResult) -> None:
    ok(f'Enabled [bold]{escape(result.mod.name)}[/bold] ({escape(result.mod.slots)})')
    for other in result.disabled_others:
        info(f'Disabled {escape(other.name)} (same group)')


def _print_mods(mods: list[InstalledMod]) -> None:
    for mod in sorted(mods, key=lambda m: m.name.casefold()):
        colour = 'green' if mod.enabled else 'dim'
        where = escape(mod.slots if mod.enabled else 'disabled')
        age = escape(format_age(mod.installed))
        console.print(f'  [{colour}]{mod.mark}[/{colour}] {escape(mod.name)}  [dim]{where} · {age}[/dim]')


def _print_grouped(mods: list[InstalledMod]) -> None:
    for group in manifest.list_groups(mods):
        rule(escape(group))
        _print_mods(manifest.group_members(mods, group))
    ungrouped = [m for m in mods if not m.group]
    if ungrouped:
        rule('Ungrouped')
        _print_mods(ungrouped)


# ----------------------------------------------------------------------
# Groups
# ----------------------------------------------------------------------

def _group_hint(group: str, matched: bool, current: str | None) -> str:
    if current and manifest.group_key(group) == manifest.group_key(current):
        return 'current'
    return 'name match' if matched else ''


def _pick_group(mods: list[InstalledMod], title: str, names: tuple,
                current: str | None = None) -> tuple[bool, str | None]:
    """Returns (chosen, group); chosen is False when the picker was cancelled."""
    suggestions = toggler.suggest_groups(mods, *names)
    items = [PickItem(g, _group_hint(g, matched, current), g) for g, matched in suggestions]
    items.append(PickItem('New group…', 'type a name, e.g. Haze skins', NEW_GROUP))
    items.append(PickItem('No group', '', NO_GROUP))
    has_match = bool(suggestions) and suggestions[0][1]
    choice = pick(title, items, default_index=0 if has_match else len(items) - 1)
    if choice is None:
        return False, None
    if choice.value is NO_GROUP:
        return True, None
    if choice.value is NEW_GROUP:
        typed = _ask_text('New group name:')
        if typed is None:
            return False, None
        return True, manifest.canonical_group(mods, typed)
    return True, choice.value


def _resolve_group(group: str | None) -> None:
    """If several members of a group are enabled, ask which one stays."""
    if not group:
        return
    enabled = [m for m in manifest.group_members(manifest.load_mods(), group) if m.enabled]
    if len(enabled) < 2:
        return
    warn(f'{len(enabled)} mods in {escape(group)} are enabled, but only one can be active.')
    choice = pick(f'Which mod should stay enabled in {group}?', _mod_items(enabled))
    if choice is None:
        warn('Left as is; run group switch later.')
        return
    _report_toggle(toggler.enable(choice.value))


def _apply_group(target: InstalledMod, group: str | None) -> int:
    updated = toggler.set_group(target, group)
    if updated.group:
        ok(f'{escape(updated.name)} is now in {escape(updated.group)}.')
        _resolve_group(updated.group)
    else:
        ok(f'{escape(updated.name)} is no longer in a group.')
    return 0


def _group_assign(args) -> int:
    mods = manifest.load_mods()
    target = _select_mod(mods, getattr(args, 'mod', None), 'Select mod to group', 'No mods installed by dlmm.')
    if target is None:
        return 0
    names = (target.name, Path(target.source).name)
    chosen, group = _pick_group(mods, f'Group for {target.name}', names, current=target.group)
    return _apply_group(target, group) if chosen else 0


def _group_set(args) -> int:
    target = _select_mod(manifest.load_mods(), args.mod, 'Select mod to group', 'No mods installed by dlmm.')
    return _apply_group(target, args.group) if target else 1


def _group_clear(args) -> int:
    target = _select_mod(manifest.load_mods(), args.mod, 'Select mod to ungroup', 'No mods installed by dlmm.')
    return _apply_group(target, None) if target else 1


def _choose_group_name(mods: list[InstalledMod], query: str | None) -> str | None:
    groups = manifest.list_groups(mods)
    if query:
        match = next((g for g in groups if manifest.group_key(g) == manifest.group_key(query.strip())), None)
        if match is None:
            fail(f'No group named {escape(query)}.')
        return match
    items = [PickItem(g, f'{len(manifest.group_members(mods, g))} mods', g) for g in groups]
    choice = pick('Select group', items)
    return choice.value if choice else None


def _group_switch(args) -> int:
    mods = manifest.load_mods()
    if not manifest.list_groups(mods):
        info('No groups yet. Assign mods to a group first (group assign).')
        return 0
    group = _choose_group_name(mods, getattr(args, 'group', None))
    if group is None:
        return 0
    members = sorted(manifest.group_members(mods, group), key=lambda m: m.name.casefold())
    items = _mod_items(members) + [PickItem('None (disable all)', '', DISABLE_ALL)]
    active = next((i for i, m in enumerate(members) if m.enabled), len(members))
    choice = pick(f'Active mod in {group}', items, default_index=active)
    if choice is None:
        return 0
    if choice.value is DISABLE_ALL:
        for mod in toggler.disable_group(group):
            ok(f'Disabled {escape(mod.name)}')
        return 0
    result = toggler.enable(choice.value)
    _report_toggle(result)
    _check_conflicts(result.mod)
    return 0


def _group_list(args) -> int:
    mods = manifest.load_mods()
    if not mods:
        info('No mods installed by dlmm.')
        return 0
    _print_grouped(mods)
    return 0


GROUP_HANDLERS = {
    'assign': _group_assign,
    'set': _group_set,
    'clear': _group_clear,
    'switch': _group_switch,
    'list': _group_list,
}


# ----------------------------------------------------------------------
# Conflicts
# ----------------------------------------------------------------------

def _report_conflict(a: InstalledMod, b: InstalledMod, conflict: conflicts.Conflict) -> None:
    warn(f'[bold]{escape(a.name)}[/bold] and [bold]{escape(b.name)}[/bold] '
         f'both replace {conflict.count} file(s):')
    for path in conflict.sample:
        dim(f'    {escape(path)}')
    if conflict.count > len(conflict.sample):
        dim(f'    … and {conflict.count - len(conflict.sample)} more')


def _fresh_pair(conflict: conflicts.Conflict) -> tuple[InstalledMod, InstalledMod] | None:
    """Current records of the pair, or None if the conflict is already resolved."""
    a = manifest.find_mod(conflict.first.mod_id)
    b = manifest.find_mod(conflict.second.mod_id)
    if a is None or b is None or not (a.enabled and b.enabled):
        return None
    if a.group and b.group and manifest.group_key(a.group) == manifest.group_key(b.group):
        return None
    return a, b


def _offer_group_for_conflict(a: InstalledMod, b: InstalledMod) -> None:
    if not confirm(f'Put {a.name} and {b.name} in the same group so only one is active?', default=True):
        return
    group = a.group or b.group
    if group is None:
        chosen, group = _pick_group(manifest.load_mods(), f'Group for {a.name} and {b.name}', (a.name, b.name))
        if not chosen or group is None:
            warn('No group chosen; nothing changed.')
            return
    for mod in (a, b):
        toggler.set_group(mod, group)
    ok(f'Both mods are now in {escape(group)}.')
    _resolve_group(group)


def _handle_conflicts(report: conflicts.ConflictReport) -> int:
    handled = 0
    for conflict in report.conflicts:
        pair = _fresh_pair(conflict)
        if pair is None:
            continue
        handled += 1
        _report_conflict(*pair, conflict)
        _offer_group_for_conflict(*pair)
    return handled


def _check_conflicts(only_with: InstalledMod | None = None) -> None:
    report = conflicts.find_conflicts(manifest.load_mods(), only_with)
    if report.unreadable:
        dim(f'{len(report.unreadable)} VPK file(s) could not be read for conflict checks (run: conflicts).')
    _handle_conflicts(report)


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------

def _group_for_install(args, mods: list[InstalledMod], name: str, archive_name: str) -> str | None:
    if getattr(args, 'no_group', False):
        return None
    preset = getattr(args, 'group', None)
    if preset:
        return manifest.canonical_group(mods, preset)
    chosen, group = _pick_group(mods, f'Is {name} part of a group?', (name, archive_name))
    return group if chosen else None


def _confirm_takeover(group: str | None, rivals: list[InstalledMod]) -> bool:
    if not rivals:
        return True
    names = ', '.join(m.name for m in rivals)
    return confirm(f'{group} already has {names} enabled. Enable the new mod and disable it?', default=True)


def _report_install(record: InstalledMod) -> None:
    where = record.slots if record.enabled else 'disabled (parked)'
    group = f' in group {escape(record.group)}' if record.group else ''
    ok(f'Installed [bold]{escape(record.name)}[/bold] as {escape(where)}{group}')
    if len(record.files) > 1:
        dim(f'{len(record.files)} files are managed together as one mod.')
    if record.enabled:
        dim('Restart Deadlock if it is running.')


def _install_flow(archive: Path, name: str, args, gamebanana_ids: tuple = (None, None)) -> int:
    game = find_game(args.game_dir)
    mods = manifest.load_mods()
    group = _group_for_install(args, mods, name, archive.name)
    rivals = [m for m in manifest.group_members(mods, group) if m.enabled]
    enabled = _confirm_takeover(group, rivals)
    request = InstallRequest(archive, name, gamebanana_ids[0], gamebanana_ids[1], group=group, enabled=enabled)
    rule(f'Installing {escape(name)}')
    record = installer.install(request, game, choose_vpks)
    if record is None:
        warn('Nothing selected; install cancelled.')
        return 0
    if record.enabled and record.group:
        for other in toggler.enable(record).disabled_others:
            info(f'Disabled {escape(other.name)} (same group)')
    _report_install(record)
    if record.enabled:
        _check_conflicts(record)
    return 0


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
    return _install_flow(archive, args.name or archive.stem, args)


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


def cmd_uninstall(args) -> int:
    target = _select_mod(manifest.load_mods(), args.name, 'Select mod to uninstall', 'No mods installed by dlmm.')
    if target is None:
        return 0
    if not confirm(f'Uninstall {target.name} ({len(target.files)} file(s))?'):
        warn('Uninstall cancelled.')
        return 0
    result = installer.uninstall(target)
    for slot in result.removed:
        ok(f'Removed {escape(slot)} (backup kept, see rollback)')
    for note in result.skipped:
        warn(f'Skipped {escape(note)}')
    _offer_unpatch(args)
    return 0


def cmd_enable(args) -> int:
    mods = manifest.load_mods()
    candidates = mods if args.name else [m for m in mods if not m.enabled]
    target = _select_mod(candidates, args.name, 'Select mod to enable', 'No disabled mods.')
    if target is None:
        return 0
    result = toggler.enable(target)
    _report_toggle(result)
    _check_conflicts(result.mod)
    return 0


def cmd_disable(args) -> int:
    mods = manifest.load_mods()
    candidates = mods if args.name else [m for m in mods if m.enabled]
    target = _select_mod(candidates, args.name, 'Select mod to disable', 'No enabled mods.')
    if target is None:
        return 0
    toggler.disable(target)
    ok(f'Disabled {escape(target.name)} (files parked, not deleted)')
    return 0


def _flip(mod: InstalledMod) -> None:
    try:
        if mod.enabled:
            toggler.disable(mod)
            ok(f'Disabled {escape(mod.name)}')
            return
        result = toggler.enable(mod)
        _report_toggle(result)
        _check_conflicts(result.mod)
    except DlmmError as e:
        fail(escape(str(e)))


def cmd_toggle(args) -> int:
    index = 0
    for _ in range(MAX_MENU_ROUNDS):
        mods = sorted(manifest.load_mods(), key=lambda m: m.name.casefold())
        if not mods:
            info('No mods installed by dlmm.')
            return 0
        choice = pick('Enable / disable mods (Enter flips, Esc when done)', _mod_items(mods), default_index=index)
        if choice is None:
            return 0
        index = mods.index(choice.value)
        _flip(choice.value)
    return 0


def _menu_group_args(args) -> argparse.Namespace:
    sub = argparse.Namespace(**vars(args))
    sub.mod = None
    sub.group = None
    return sub


def cmd_group(args) -> int:
    action = getattr(args, 'group_action', None)
    if action is not None:
        return GROUP_HANDLERS[action](args)
    choice = pick('Mod groups', [PickItem(label, '', key) for label, key in GROUP_ACTIONS])
    if choice is None:
        return 0
    return GROUP_HANDLERS[choice.value](_menu_group_args(args))


def cmd_list(args) -> int:
    return _group_list(args)


def cmd_conflicts(args) -> int:
    report = conflicts.find_conflicts(manifest.load_mods())
    for problem in report.unreadable:
        dim(f'Unreadable: {escape(problem)}')
    if _handle_conflicts(report) == 0:
        ok('No enabled mods replace the same files.')
    return 0


def _report_patch_state(state: str) -> None:
    if state == gameinfo.PATCHED:
        ok('gameinfo.gi: loads citadel/addons')
    elif state == gameinfo.INCOMPLETE:
        warn('gameinfo.gi: patch incomplete (missing Mod/Write paths, game will crash) - run: patch')
    else:
        warn('gameinfo.gi: not patched (run: patch)')


def _status_mods(mods: list[InstalledMod]) -> None:
    disabled = sum(1 for m in mods if not m.enabled)
    groups = manifest.list_groups(mods)
    info(f'Managed mods: {len(mods)} ({disabled} disabled, {len(groups)} groups)')
    for group in groups:
        active = [m for m in manifest.group_members(mods, group) if m.enabled]
        if len(active) > 1:
            warn(f'Group {escape(group)} has {len(active)} enabled mods (run: group switch).')
    report = conflicts.find_conflicts(mods)
    if report.conflicts:
        warn(f'{len(report.conflicts)} conflict(s) between enabled mods (run: conflicts).')


def _status_game(args) -> None:
    try:
        game = find_game(args.game_dir)
    except DlmmError as e:
        fail(escape(str(e)))
        return
    ok(f'Game folder: {escape(str(game.citadel))}')
    try:
        state = gameinfo.patch_state(gameinfo.read_gameinfo(game.gameinfo))
    except DlmmError as e:
        fail(f'gameinfo.gi: {escape(str(e))}')
    else:
        _report_patch_state(state)
    mods = manifest.load_mods()
    _status_mods(mods)
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


def _backup_hint(entry: backups.BackupEntry) -> str:
    pinned = ' · original' if entry.pinned else ''
    return f'{format_size(entry.size)} · {format_age(entry.created)}{pinned}'


def _warn_if_stale_gameinfo(entry: backups.BackupEntry) -> None:
    if entry.stored_file.lower() != 'gameinfo.gi':
        return
    try:
        old_text = gameinfo.read_gameinfo(entry.stored_path)
        current_text = gameinfo.read_gameinfo(Path(entry.original_path))
        stale = gameinfo.base_layout(old_text) != gameinfo.base_layout(current_text)
    except DlmmError as e:
        warn(f'Could not compare with the current gameinfo.gi: {escape(str(e))}')
        return
    if stale:
        warn('This backup is from an older game version; restoring it may break the game. '
             'Prefer Steam verify + patch instead.')


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
    _warn_if_stale_gameinfo(entry)
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
        info('gameinfo.gi is already patched.')
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
    set_setting(PROTOCOL_PROMPT_SETTING, None)
    ok('deadlock-mod-manager: links now open dlmm.')
    if previous:
        dim(f'Previous handler saved; unregister restores it: {escape(previous)}')
    info('Click 1-click install on a GameBanana Deadlock mod to try it.')
    return 0


def cmd_unregister(args) -> int:
    result = protocol.unregister()
    set_setting(PROTOCOL_PROMPT_SETTING, True)
    ok(f'Unregister: {escape(result)}')
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
    return _install_flow(path, remote.mod_name, args, (link.item_id, link.file_id))


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
    'enable': cmd_enable,
    'disable': cmd_disable,
    'toggle': cmd_toggle,
    'group': cmd_group,
    'list': cmd_list,
    'conflicts': cmd_conflicts,
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
        state = gameinfo.patch_state(gameinfo.read_gameinfo(game.gameinfo))
    except DlmmError as e:
        warn(escape(str(e)))
        return
    if state == gameinfo.PATCHED:
        return
    if state == gameinfo.INCOMPLETE:
        warn('gameinfo.gi is missing Mod/Write search paths - the game will crash on launch with mods.')
    else:
        warn('gameinfo.gi lost its addons entry (usually a game update) - your mods are inactive.')
    if confirm('Re-apply the patch now?', default=True):
        gameinfo.ensure_patched(game.gameinfo)
        ok('gameinfo.gi patched (backup saved).')


def _remember_prompt_declined() -> None:
    try:
        set_setting(PROTOCOL_PROMPT_SETTING, True)
    except DlmmError as e:
        warn(f'Could not save your answer: {escape(str(e))}')


def _offer_protocol_takeover() -> None:
    """Ask once (Windows, interactive menu only) whether dlmm should handle 1-click links."""
    if sys.platform != 'win32':
        return
    try:
        if get_setting(PROTOCOL_PROMPT_SETTING) is not None:
            return
        command = protocol.read_command()
    except DlmmError as e:
        warn(f'Could not check the 1-click handler: {escape(str(e))}')
        return
    if protocol.is_ours(command):
        return
    if command:
        dim('1-click links currently open another app; it will be backed up and unregister restores it.')
    if not confirm(PROTOCOL_PROMPT, default=True):
        _remember_prompt_declined()
        dim('No problem. You can enable it later with: python deadlock_mm.py register')
        return
    try:
        cmd_register(None)
    except DlmmError as e:
        warn(f'Could not register the 1-click handler: {escape(str(e))}')


def _menu_args(args) -> argparse.Namespace:
    return argparse.Namespace(
        game_dir=args.game_dir, downloads=args.downloads, archive=None, name=None,
        group=None, no_group=False, group_action=None, mod=None,
    )


def _run_menu_command(command: str, args) -> None:
    try:
        COMMANDS[command](_menu_args(args))
    except DlmmError as e:
        fail(escape(str(e)))


def interactive(args) -> int:
    console.print(Panel(f'Deadlock Lite Mod Manager [dim]v{__version__}[/dim]', border_style='blue'))
    _offer_protocol_takeover()
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

def _add_install_parser(sub) -> None:
    install = sub.add_parser('install', help='Install a mod archive (picker over Downloads if omitted)')
    install.add_argument('archive', nargs='?', help='Path to a .rar/.zip/.7z/.vpk file')
    install.add_argument('--name', help='Display name for the mod (default: archive name)')
    grouping = install.add_mutually_exclusive_group()
    grouping.add_argument('--group', help='Put the mod in this group (skips the group prompt)')
    grouping.add_argument('--no-group', dest='no_group', action='store_true', help='Do not ask about groups')


def _add_group_parser(sub) -> None:
    group_parser = sub.add_parser('group', help='Manage mod groups (only one mod per group is enabled)')
    actions = group_parser.add_subparsers(dest='group_action')
    actions.add_parser('list', help='Show groups and their members')
    switch = actions.add_parser('switch', help='Choose the active mod of a group')
    switch.add_argument('group', nargs='?')
    assign = actions.add_parser('assign', help='Pick a mod and a group interactively')
    assign.add_argument('mod', nargs='?')
    set_parser = actions.add_parser('set', help='Put a mod in a group')
    set_parser.add_argument('mod')
    set_parser.add_argument('group')
    clear = actions.add_parser('clear', help='Remove a mod from its group')
    clear.add_argument('mod')


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
    _add_install_parser(sub)
    for name, text in (('uninstall', 'Uninstall a mod'), ('enable', 'Enable a mod'), ('disable', 'Disable a mod')):
        named = sub.add_parser(name, help=text)
        named.add_argument('name', nargs='?', help='Mod name (picker if omitted)')
    _add_group_parser(sub)
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
