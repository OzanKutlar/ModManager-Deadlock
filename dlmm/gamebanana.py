"""Resolve GameBanana 1-click links and download the referenced file.

Link format: deadlock-mod-manager:https://gamebanana.com/mmdl/<file_id>,Mod,<mod_id>
The file id is verified against the mod's public file list before anything is downloaded.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .archives import MOD_EXTS
from .errors import GameBananaError, LinkError
from .fsutil import discard_file

API_BASE = 'https://gamebanana.com/apiv11'
DOWNLOAD_BASE = 'https://gamebanana.com/dl'
USER_AGENT = f'dlmm/{__version__} (lightweight Deadlock mod manager)'
TIMEOUT_SECONDS = 30
MAX_API_BYTES = 4 * 1024 ** 2
MAX_DOWNLOAD_BYTES = 4 * 1024 ** 3
MAX_FILES = 500
MAX_NAME_LEN = 150
MAX_UNIQUE_ATTEMPTS = 1_000
CHUNK = 1 << 20
BACKSLASH = chr(92)
LINK_RE = re.compile(
    r'^deadlock-mod-manager:(?://)?https://(?:www\.)?gamebanana\.com/(?:mmdl|dl)/'
    r'(\d{1,12}),([A-Za-z]{1,32}),(\d{1,12})/?$'
)
LINK_FORMAT = 'deadlock-mod-manager:https://gamebanana.com/mmdl/<file>,Mod,<mod> (or /dl/)'
INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
)

ProgressFn = Callable[[int, int], None]


@dataclass(frozen=True)
class ModLink:
    file_id: int
    item_type: str
    item_id: int


@dataclass(frozen=True)
class RemoteFile:
    file_id: int
    mod_id: int
    mod_name: str
    filename: str
    size: int

    @property
    def download_url(self) -> str:
        return f'{DOWNLOAD_BASE}/{self.file_id}'


# ----------------------------------------------------------------------
# Parsing & validation
# ----------------------------------------------------------------------

def parse_mod_link(raw: str) -> ModLink:
    if not isinstance(raw, str):
        raise LinkError('Link must be a string.')
    clean = urllib.parse.unquote(raw.strip().strip('"').strip("'")).strip()
    match = LINK_RE.match(clean)
    if match is None:
        raise LinkError(f'Not a valid GameBanana 1-click link: {raw!r} (expected {LINK_FORMAT})')
    if match.group(2) != 'Mod':
        raise LinkError(f'Unsupported item type {match.group(2)!r} (only Mod is supported).')
    return ModLink(int(match.group(1)), match.group(2), int(match.group(3)))


def safe_filename(name, fallback: str) -> str:
    base = name.replace(BACKSLASH, '/').rsplit('/', 1)[-1] if isinstance(name, str) else ''
    clean = INVALID_NAME_RE.sub('_', base).strip(' .')[:MAX_NAME_LEN]
    if not clean:
        return fallback
    if clean.split('.', 1)[0].upper() in RESERVED_NAMES:
        return '_' + clean
    return clean


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------

def _get_json(url: str):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_API_BYTES + 1)
    except (urllib.error.URLError, OSError) as e:
        raise GameBananaError(f'GameBanana API request failed: {e}') from e
    if len(body) > MAX_API_BYTES:
        raise GameBananaError('GameBanana API response is unexpectedly large.')
    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as e:
        raise GameBananaError(f'GameBanana API returned invalid JSON: {e}') from e


def _find_file(files: list, file_id: int) -> dict | None:
    for entry in files[:MAX_FILES]:
        if isinstance(entry, dict) and _as_int(entry.get('_idRow')) == file_id:
            return entry
    return None


def _remote_file(entry: dict, link: ModLink, name) -> RemoteFile:
    mod_name = name.strip() if isinstance(name, str) and name.strip() else f'GameBanana mod {link.item_id}'
    filename = safe_filename(entry.get('_sFile'), f'gamebanana_{link.file_id}.zip')
    if not filename.lower().endswith(MOD_EXTS):
        raise GameBananaError(f'Unsupported file type: {filename}')
    size = _as_int(entry.get('_nFilesize')) or 0
    if size > MAX_DOWNLOAD_BYTES:
        raise GameBananaError(f'File is too large ({size} bytes).')
    return RemoteFile(link.file_id, link.item_id, mod_name[:MAX_NAME_LEN], filename, size)


def fetch_remote_file(link: ModLink) -> RemoteFile:
    """Look up the mod and confirm the linked file really belongs to it."""
    if not isinstance(link, ModLink):
        raise GameBananaError('Invalid link object.')
    query = urllib.parse.urlencode({'_csvProperties': '_sName,_aFiles'})
    data = _get_json(f'{API_BASE}/Mod/{link.item_id}?{query}')
    if not isinstance(data, dict):
        raise GameBananaError('Unexpected GameBanana API response.')
    files = data.get('_aFiles')
    if not isinstance(files, list) or not files:
        raise GameBananaError('Mod has no downloadable files (it may be private, withheld or deleted).')
    entry = _find_file(files, link.file_id)
    if entry is None:
        raise GameBananaError(f'File {link.file_id} is not part of mod {link.item_id}; refusing to download.')
    return _remote_file(entry, link, data.get('_sName'))


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------

def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, MAX_UNIQUE_ATTEMPTS):
        candidate = path.with_name(f'{path.stem} ({index}){path.suffix}')
        if not candidate.exists():
            return candidate
    raise GameBananaError(f'Could not find a free file name for {path.name}.')


def _stream(response, handle, expected: int, on_progress: ProgressFn | None) -> int:
    written = 0
    for _ in range(MAX_DOWNLOAD_BYTES // CHUNK + 2):
        chunk = response.read(CHUNK)
        if not chunk:
            return written
        written += len(chunk)
        if written > MAX_DOWNLOAD_BYTES:
            break
        handle.write(chunk)
        if on_progress is not None:
            on_progress(written, expected)
    raise GameBananaError('Download exceeds the size limit; aborted.')


def _fetch_to(remote: RemoteFile, part: Path, on_progress: ProgressFn | None) -> int:
    request = urllib.request.Request(remote.download_url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, open(part, 'wb') as handle:
            if urllib.parse.urlparse(response.geturl()).scheme != 'https':
                raise GameBananaError('Download was redirected to a non-HTTPS URL; aborted.')
            return _stream(response, handle, remote.size, on_progress)
    except (urllib.error.URLError, OSError) as e:
        raise GameBananaError(f'Download failed: {e}') from e


def download(remote: RemoteFile, dest_dir: Path, on_progress: ProgressFn | None = None) -> Path:
    """Download to dest_dir via a .part file. Returns the final path."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise GameBananaError(f'Cannot create {dest_dir}: {e}') from e
    target = _unique_path(dest_dir / remote.filename)
    part = target.with_name(target.name + '.part')
    try:
        written = _fetch_to(remote, part, on_progress)
        if remote.size and written != remote.size:
            raise GameBananaError(f'Download incomplete ({written} of {remote.size} bytes).')
        os.replace(part, target)
    except BaseException:
        discard_file(part)
        raise
    return target
