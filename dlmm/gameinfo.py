"""Read, patch and unpatch the SearchPaths block of Deadlock's gameinfo.gi.

Deadlock only loads citadel/addons when SearchPaths starts with 'Game citadel/addons'.
Every write goes through backups.write_text_with_backup; the very first backup is pinned.
"""

import re
from pathlib import Path

from .backups import BackupEntry, has_pinned_backup, write_text_with_backup
from .errors import BackupError, GameInfoError

ADDONS_PATH = 'citadel/addons'
TAB = chr(9)
LF = chr(10)
CRLF = chr(13) + chr(10)
BACKSLASH = chr(92)
ADDONS_LINE = 'Game' + TAB * 4 + ADDONS_PATH
DEFAULT_INDENT = TAB * 3
MAX_BLOCK_CHARS = 20_000
MAX_GAMEINFO_BYTES = 1_000_000
SEARCHPATHS_RE = re.compile(r'^[ \t]*SearchPaths\b', re.MULTILINE)


def read_gameinfo(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_GAMEINFO_BYTES:
            raise GameInfoError(f'{path} is unexpectedly large; refusing to edit it.')
        with open(path, 'r', encoding='utf-8', newline='') as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GameInfoError(f'Cannot read {path}: {e}') from e


def _newline(text: str) -> str:
    return CRLF if CRLF in text else LF


def _find_block(text: str) -> tuple[int, int]:
    """Returns (index of '{', index of the matching '}') of the SearchPaths block."""
    match = SEARCHPATHS_RE.search(text)
    if match is None:
        raise GameInfoError('No SearchPaths block found in gameinfo.gi.')
    open_idx = text.find('{', match.end())
    if open_idx < 0 or text[match.end():open_idx].strip():
        raise GameInfoError('SearchPaths is not followed by a { block.')
    depth = 0
    for idx in range(open_idx, min(len(text), open_idx + MAX_BLOCK_CHARS)):
        if text[idx] == '{':
            depth += 1
        elif text[idx] == '}':
            depth -= 1
            if depth == 0:
                return open_idx, idx
    raise GameInfoError('SearchPaths block has unbalanced braces.')


def _split_block(text: str) -> tuple[str, list[str], str, str]:
    open_idx, close_idx = _find_block(text)
    nl = _newline(text)
    inner = text[open_idx + 1:close_idx]
    return text[:open_idx + 1], inner.split(nl), text[close_idx:], nl


def _tokens(line: str) -> list[str]:
    return line.split('//', 1)[0].split()


def _is_addons_entry(line: str) -> bool:
    tokens = _tokens(line)
    if len(tokens) < 2 or tokens[0].strip('"').lower() != 'game':
        return False
    value = tokens[1].strip('"').replace(BACKSLASH, '/').rstrip('/').lower()
    return value == ADDONS_PATH


def _first_entry(lines: list[str]) -> str | None:
    for line in lines:
        if _tokens(line):
            return line
    return None


def _indent_of(line: str | None) -> str:
    if line is None:
        return DEFAULT_INDENT
    return line[:len(line) - len(line.lstrip())]


def is_patched(text: str) -> bool:
    _head, lines, _tail, _nl = _split_block(text)
    first = _first_entry(lines)
    return first is not None and _is_addons_entry(first)


def patch(text: str) -> str:
    """Return text with 'Game citadel/addons' as the first SearchPaths entry."""
    if is_patched(text):
        return text
    head, lines, tail, nl = _split_block(text)
    kept = [line for line in lines if not _is_addons_entry(line)]
    entry = _indent_of(_first_entry(kept)) + ADDONS_LINE
    if kept and not kept[0].strip():
        new_lines = [kept[0], entry, *kept[1:]]
    else:
        new_lines = ['', entry, *kept]
    return head + nl.join(new_lines) + tail


def unpatch(text: str) -> str:
    """Return text with every citadel/addons entry removed from SearchPaths."""
    head, lines, tail, nl = _split_block(text)
    kept = [line for line in lines if not _is_addons_entry(line)]
    return head + nl.join(kept) + tail


def _write(path: Path, new_text: str, reason: str) -> BackupEntry | None:
    pinned = not has_pinned_backup(path)
    try:
        return write_text_with_backup(path, new_text, reason, pinned=pinned)
    except BackupError as e:
        raise GameInfoError(f'Could not update {path}: {e}') from e


def ensure_patched(path: Path) -> bool:
    """Patch gameinfo.gi if needed. Returns True when the file was changed."""
    text = read_gameinfo(path)
    new_text = patch(text)
    if new_text == text:
        return False
    _write(path, new_text, 'before adding citadel/addons to SearchPaths')
    return True


def remove_patch(path: Path) -> bool:
    """Remove the addons entry. Returns True when the file was changed."""
    text = read_gameinfo(path)
    new_text = unpatch(text)
    if new_text == text:
        return False
    _write(path, new_text, 'before removing citadel/addons from SearchPaths')
    return True
