"""Minimal read-only parser for Valve VPK (v1/v2) directory trees.

Only the directory tree is read (never file data) to list the paths a mod replaces.
Tree layout: extension -> path -> filename (null-terminated strings, each level ended by an
empty string), each file followed by an 18-byte entry and optional preload bytes.
"""

import struct
from pathlib import Path

from .errors import VpkError

VPK_SIGNATURE = 0x55AA1234
HEADER = struct.Struct('<III')
HEADER_V2_EXTRA = struct.Struct('<IIII')
ENTRY = struct.Struct('<IHHIIH')
ENTRY_TERMINATOR = 0xFFFF
MAX_TREE_BYTES = 64 * 1024 ** 2
MAX_ENTRIES = 500_000
MAX_NAME_BYTES = 1_024
NUL = b'\x00'
NONE_MARKER = ' '


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def cstring(self) -> str:
        end = self.data.find(NUL, self.pos, self.pos + MAX_NAME_BYTES + 1)
        if end < 0:
            raise VpkError('Unterminated name in VPK tree.')
        raw = self.data[self.pos:end]
        self.pos = end + 1
        return raw.decode('utf-8', errors='replace')

    def skip_entry(self) -> None:
        if self.pos + ENTRY.size > len(self.data):
            raise VpkError('Truncated VPK entry.')
        _crc, preload, _index, _offset, _length, terminator = ENTRY.unpack_from(self.data, self.pos)
        if terminator != ENTRY_TERMINATOR:
            raise VpkError('Corrupt VPK entry.')
        self.pos += ENTRY.size + preload
        if self.pos > len(self.data):
            raise VpkError('Truncated VPK preload data.')


def _read_exact(handle, size: int, what: str) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise VpkError(f'Truncated VPK {what}.')
    return data


def _read_tree(path: Path) -> bytes:
    try:
        with open(path, 'rb') as handle:
            signature, version, tree_size = HEADER.unpack(_read_exact(handle, HEADER.size, 'header'))
            if signature != VPK_SIGNATURE:
                raise VpkError('Not a VPK file (bad signature).')
            if version not in (1, 2):
                raise VpkError(f'Unsupported VPK version {version}.')
            if version == 2:
                _read_exact(handle, HEADER_V2_EXTRA.size, 'header')
            if tree_size > MAX_TREE_BYTES:
                raise VpkError('VPK directory tree is unexpectedly large.')
            return _read_exact(handle, tree_size, 'directory tree')
    except OSError as e:
        raise VpkError(f'Cannot read {path.name}: {e}') from e


def _join(directory: str, name: str, ext: str) -> str:
    filename = name if ext in ('', NONE_MARKER) else f'{name}.{ext}'
    if directory in ('', NONE_MARKER):
        return filename.lower()
    return f'{directory.strip("/")}/{filename}'.lower()


def _parse_dir(cursor: _Cursor, ext: str, directory: str, files: set[str], budget: int) -> int:
    for _ in range(MAX_ENTRIES):
        name = cursor.cstring()
        if not name:
            return budget
        cursor.skip_entry()
        files.add(_join(directory, name, ext))
        budget -= 1
        if budget <= 0:
            raise VpkError('VPK lists too many files.')
    raise VpkError('VPK directory has too many files.')


def _parse_ext(cursor: _Cursor, ext: str, files: set[str], budget: int) -> int:
    for _ in range(MAX_ENTRIES):
        directory = cursor.cstring()
        if not directory:
            return budget
        budget = _parse_dir(cursor, ext, directory, files, budget)
    raise VpkError('VPK has too many directories.')


def list_files(path: Path) -> frozenset[str]:
    """All file paths inside the VPK, lower-case with forward slashes."""
    cursor = _Cursor(_read_tree(Path(path)))
    files: set[str] = set()
    budget = MAX_ENTRIES
    for _ in range(MAX_ENTRIES):
        ext = cursor.cstring()
        if not ext:
            return frozenset(files)
        budget = _parse_ext(cursor, ext, files, budget)
    raise VpkError('VPK has too many extensions.')
