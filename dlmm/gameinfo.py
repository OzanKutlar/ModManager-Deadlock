"""Read, patch and unpatch the SearchPaths block of Deadlock's gameinfo.gi.

Deadlock only loads citadel/addons when SearchPaths starts with 'Game citadel/addons'.
Since the 2026-09-29 game update the stock file no longer lists Mod/Write paths; the
engine then treats the first Game path (our addons folder) as the mod folder and crashes
looking for cfg/user_keys_default.vcfg. patch() therefore also adds any missing
Mod/Write citadel/core entries. Every write is backed up; the first backup is pinned.
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
ENTRY_GAP = TAB * 4
ADDONS_LINE = 'Game' + ENTRY_GAP + ADDONS_PATH
ADDONS_KEY = ('game', ADDONS_PATH)
REQUIRED_ENTRIES = (('Mod', 'citadel'), ('Write', 'citadel'), ('Mod', 'core'), ('Write', 'core'))
DEFAULT_INDENT = TAB * 3
MAX_BLOCK_CHARS = 20_000
MAX_GAMEINFO_BYTES = 1_000_000
SEARCHPATHS_RE = re.compile(r'^[ \t]*SearchPaths\b', re.MULTILINE)
PATCHED, INCOMPLETE, UNPATCHED = 'patched', 'incomplete', 'unpatched'


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


def _entry_key(line: str) -> tuple[str, str] | None:
    tokens = _tokens(line)
    if len(tokens) < 2:
        return None
    kind = tokens[0].strip('"').lower()
    value = tokens[1].strip('"').replace(BACKSLASH, '/').rstrip('/').lower()
    return kind, value


def _is_addons_entry(line: str) -> bool:
    return _entry_key(line) == ADDONS_KEY


def _required_key(entry: tuple[str, str]) -> tuple[str, str]:
    return entry[0].lower(), entry[1].lower()


def _missing_required(lines: list[str]) -> list[tuple[str, str]]:
    present = {_entry_key(line) for line in lines}
    return [entry for entry in REQUIRED_ENTRIES if _required_key(entry) not in present]


def _is_managed(line: str) -> bool:
    key = _entry_key(line)
    return key == ADDONS_KEY or key in {_required_key(e) for e in REQUIRED_ENTRIES}


def _first_entry(lines: list[str]) -> str | None:
    for line in lines:
        if _tokens(line):
            return line
    return None


def _indent_of(line: str | None) -> str:
    if line is None:
        return DEFAULT_INDENT
    return line[:len(line) - len(line.lstrip())]


def patch_state(text: str) -> str:
    """PATCHED, INCOMPLETE (addons first but Mod/Write paths missing) or UNPATCHED."""
    _head, lines, _tail, _nl = _split_block(text)
    first = _first_entry(lines)
    if first is None or not _is_addons_entry(first):
        return UNPATCHED
    return INCOMPLETE if _missing_required(lines) else PATCHED


def is_patched(text: str) -> bool:
    return patch_state(text) == PATCHED


def _with_addons_first(kept: list[str], entry: str) -> list[str]:
    if kept and not kept[0].strip():
        return [kept[0], entry, *kept[1:]]
    return ['', entry, *kept]


def _with_required_last(lines: list[str], extra: list[str]) -> list[str]:
    if not extra:
        return lines
    if len(lines) > 1 and not lines[-1].strip():
        return [*lines[:-1], *extra, lines[-1]]
    return [*lines, *extra, '']


def patch(text: str) -> str:
    """Addons first, plus any missing Mod/Write citadel/core entries at the end of the block."""
    if is_patched(text):
        return text
    head, lines, tail, nl = _split_block(text)
    kept = [line for line in lines if not _is_addons_entry(line)]
    indent = _indent_of(_first_entry(kept))
    extra = [indent + kind + ENTRY_GAP + path for kind, path in _missing_required(kept)]
    new_lines = _with_required_last(_with_addons_first(kept, indent + ADDONS_LINE), extra)
    return head + nl.join(new_lines) + tail


def unpatch(text: str) -> str:
    """Remove every citadel/addons entry (Mod/Write lines are harmless and kept)."""
    head, lines, tail, nl = _split_block(text)
    kept = [line for line in lines if not _is_addons_entry(line)]
    return head + nl.join(kept) + tail


def base_layout(text: str) -> str:
    """The file without dlmm-managed SearchPaths lines, for comparing game versions."""
    head, lines, tail, _nl = _split_block(text)
    kept = [line.strip() for line in lines if line.strip() and not _is_managed(line)]
    return head.replace(CRLF, LF) + LF.join(kept) + tail.replace(CRLF, LF)


def _write(path: Path, new_text: str, reason: str) -> BackupEntry | None:
    pinned = not has_pinned_backup(path)
    try:
        return write_text_with_backup(path, new_text, reason, pinned=pinned)
    except BackupError as e:
        raise GameInfoError(f'Could not update {path}: {e}') from e


def ensure_patched(path: Path) -> bool:
    """Patch (or repair) gameinfo.gi if needed. Returns True when the file was changed."""
    text = read_gameinfo(path)
    new_text = patch(text)
    if new_text == text:
        return False
    _write(path, new_text, 'before patching SearchPaths (addons + Mod/Write paths)')
    return True


def remove_patch(path: Path) -> bool:
    """Remove the addons entry. Returns True when the file was changed."""
    text = read_gameinfo(path)
    new_text = unpatch(text)
    if new_text == text:
        return False
    _write(path, new_text, 'before removing citadel/addons from SearchPaths')
    return True
