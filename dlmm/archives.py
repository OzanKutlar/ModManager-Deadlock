"""Find mod archives in Downloads and safely extract only their .vpk files.

Nothing from an archive is ever executed; only .vpk members are written, into a temp folder,
after path-traversal, symlink, member-count and size checks.
"""

import importlib.util
import os
import re
import shutil
import stat
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import ArchiveError
from .fsutil import format_age, format_size

ARCHIVE_EXTS = ('.rar', '.zip', '.7z')
MOD_EXTS = ARCHIVE_EXTS + ('.vpk',)
VPK_EXT = '.vpk'
MAX_SCAN_ENTRIES = 20_000
MAX_MEMBERS = 5_000
MAX_TOTAL_BYTES = 8 * 1024 ** 3
COPY_CHUNK = 1 << 20
BACKSLASH = chr(92)
DRIVE_RE = re.compile(r'^[A-Za-z]:')
RAR_FLAGS = ('unrar', 'sevenzip', 'sevenzip2', 'bsdtar')


@dataclass(frozen=True)
class ArchiveInfo:
    name: str
    path: Path
    downloaded: float
    size: int

    @property
    def hint(self) -> str:
        return f'{format_size(self.size)} · downloaded {format_age(self.downloaded)}'


@dataclass(frozen=True)
class ExtractedVpk:
    relative: str
    path: Path
    size: int


@dataclass(frozen=True)
class RarTool:
    flag: str
    attr: str
    path: str


# ----------------------------------------------------------------------
# Downloads scan
# ----------------------------------------------------------------------

def _download_time(st: os.stat_result) -> float:
    """Creation time is when the browser created the file; mtime covers copies."""
    birth = getattr(st, 'st_birthtime', None)
    if birth is None and sys.platform == 'win32':
        birth = st.st_ctime
    return max(birth or 0.0, st.st_mtime)


def _archive_info(entry: os.DirEntry) -> ArchiveInfo | None:
    if not entry.name.lower().endswith(MOD_EXTS):
        return None
    try:
        if not entry.is_file(follow_symlinks=False):
            return None
        st = entry.stat()
    except OSError:
        return None  # file vanished or is locked mid-download; skip it
    return ArchiveInfo(entry.name, Path(entry.path), _download_time(st), st.st_size)


def scan_downloads(folder: Path) -> list[ArchiveInfo]:
    """Mod archives in `folder`, most recently downloaded first."""
    found: list[ArchiveInfo] = []
    try:
        with os.scandir(folder) as entries:
            for count, entry in enumerate(entries):
                if count >= MAX_SCAN_ENTRIES:
                    break
                item = _archive_info(entry)
                if item is not None:
                    found.append(item)
    except OSError as e:
        raise ArchiveError(f'Cannot scan {folder}: {e}') from e
    found.sort(key=lambda a: (-a.downloaded, a.name.lower()))
    return found


# ----------------------------------------------------------------------
# Safety helpers
# ----------------------------------------------------------------------

def safe_member_path(dest: Path, member_name: str) -> Path:
    """Map an archive member into `dest`, rejecting absolute paths and traversal."""
    if not isinstance(member_name, str) or not member_name:
        raise ArchiveError('Archive contains an unnamed member.')
    clean = member_name.replace(BACKSLASH, '/')
    if clean.startswith('/') or DRIVE_RE.match(clean):
        raise ArchiveError(f'Unsafe absolute path in archive: {member_name}')
    parts = [part for part in clean.split('/') if part not in ('', '.')]
    if not parts or '..' in parts or any(':' in part for part in parts):
        raise ArchiveError(f'Unsafe path in archive: {member_name}')
    root = dest.resolve()
    target = root.joinpath(*parts).resolve()
    if not target.is_relative_to(root):
        raise ArchiveError(f'Archive member escapes the extraction folder: {member_name}')
    return target


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def _copy_bounded(source, handle, limit: int) -> int:
    written = 0
    for _ in range(limit // COPY_CHUNK + 2):
        chunk = source.read(COPY_CHUNK)
        if not chunk:
            return written
        written += len(chunk)
        if written > limit:
            break
        handle.write(chunk)
    raise ArchiveError('Archive member exceeds the size limit.')


def _vpk_members(infos: list, is_dir, is_link) -> list:
    if len(infos) > MAX_MEMBERS:
        raise ArchiveError(f'Archive has {len(infos)} entries (limit {MAX_MEMBERS}); refusing.')
    chosen = [
        info for info in infos
        if not is_dir(info) and not is_link(info) and info.filename.lower().endswith(VPK_EXT)
    ]
    total = sum(max(0, int(info.file_size)) for info in chosen)
    if total > MAX_TOTAL_BYTES:
        raise ArchiveError(f'Archive would extract {format_size(total)} of VPKs; refusing.')
    return chosen


def _extract_listed(container, members: list, dest: Path) -> list[ExtractedVpk]:
    results: list[ExtractedVpk] = []
    for info in members:
        target = safe_member_path(dest, info.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        with container.open(info) as source, open(target, 'wb') as handle:
            size = _copy_bounded(source, handle, MAX_TOTAL_BYTES)
        results.append(ExtractedVpk(_relative(dest, target), target, size))
    return results


def collect_vpks(root: Path) -> list[ExtractedVpk]:
    """Regular .vpk files under `root` (symlinks and escapes ignored)."""
    base = root.resolve()
    results: list[ExtractedVpk] = []
    for count, path in enumerate(base.rglob('*')):
        if count >= MAX_MEMBERS * 2:
            raise ArchiveError('Too many extracted files.')
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != VPK_EXT:
            continue
        resolved = path.resolve()
        if resolved.is_relative_to(base):
            results.append(ExtractedVpk(_relative(base, resolved), resolved, resolved.stat().st_size))
    return results


# ----------------------------------------------------------------------
# RAR backend discovery
# ----------------------------------------------------------------------

def _program_files(*parts: str) -> tuple[str, ...]:
    if sys.platform != 'win32':
        return ()
    roots = (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'))
    return tuple(str(Path(root).joinpath(*parts)) for root in roots if root)


def _rar_candidates() -> tuple:
    tar_names = ('bsdtar', 'tar') if sys.platform == 'win32' else ('bsdtar',)
    return (
        ('unrar', 'UNRAR_TOOL', ('unrar',), _program_files('WinRAR', 'UnRAR.exe')),
        ('sevenzip', 'SEVENZIP_TOOL', ('7z',), _program_files('7-Zip', '7z.exe')),
        ('sevenzip2', 'SEVENZIP2_TOOL', ('7zz',), ()),
        ('bsdtar', 'BSDTAR_TOOL', tar_names, ()),
    )


def find_rar_tool() -> RarTool | None:
    """First usable RAR extractor: UnRAR, 7-Zip, 7zz, then bsdtar (Windows tar.exe)."""
    for flag, attr, names, extra in _rar_candidates():
        on_path = next((p for p in (shutil.which(n) for n in names) if p), None)
        fixed = next((p for p in extra if os.path.isfile(p)), None)
        path = on_path or fixed
        if path:
            return RarTool(flag, attr, path)
    return None


def has_py7zr() -> bool:
    return importlib.util.find_spec('py7zr') is not None


def _load_rarfile():
    try:
        import rarfile
    except ImportError as e:
        raise ArchiveError('The rarfile package is missing: pip install rarfile') from e
    tool = find_rar_tool()
    if tool is None:
        raise ArchiveError('No RAR extractor found. Install 7-Zip (https://7-zip.org) or WinRAR.')
    setattr(rarfile, tool.attr, tool.path)
    flags = {name: name == tool.flag for name in RAR_FLAGS}
    try:
        rarfile.tool_setup(unar=False, force=True, **flags)
    except rarfile.Error as e:
        raise ArchiveError(f'RAR extractor {tool.path} is not usable: {e}') from e
    return rarfile


# ----------------------------------------------------------------------
# Extractors
# ----------------------------------------------------------------------

def _zip_is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _extract_zip(archive: Path, dest: Path) -> list[ExtractedVpk]:
    try:
        with zipfile.ZipFile(archive) as zf:
            members = _vpk_members(zf.infolist(), lambda i: i.is_dir(), _zip_is_link)
            return _extract_listed(zf, members, dest)
    except (zipfile.BadZipFile, RuntimeError, OSError) as e:
        raise ArchiveError(f'Cannot extract {archive.name}: {e}') from e


def _extract_rar(archive: Path, dest: Path) -> list[ExtractedVpk]:
    rarfile = _load_rarfile()
    try:
        with rarfile.RarFile(str(archive)) as rf:
            members = _vpk_members(rf.infolist(), lambda i: i.is_dir(), lambda i: i.is_symlink())
            return _extract_listed(rf, members, dest)
    except (rarfile.Error, OSError) as e:
        raise ArchiveError(f'Cannot extract {archive.name}: {e}') from e


def _py7zr_errors(module) -> tuple:
    names = ('ArchiveError', 'Bad7zFile', 'PasswordRequired')
    found = tuple(getattr(module, n) for n in names if isinstance(getattr(module, n, None), type))
    return found + (OSError, ValueError)


def _7z_vpk_names(infos: list) -> list[str]:
    if len(infos) > MAX_MEMBERS:
        raise ArchiveError(f'Archive has {len(infos)} entries (limit {MAX_MEMBERS}); refusing.')
    chosen = [i for i in infos if not i.is_directory and i.filename.lower().endswith(VPK_EXT)]
    total = sum(max(0, int(i.uncompressed or 0)) for i in chosen)
    if total > MAX_TOTAL_BYTES:
        raise ArchiveError(f'Archive would extract {format_size(total)} of VPKs; refusing.')
    return [i.filename for i in chosen]


def _extract_7z(archive: Path, dest: Path) -> list[ExtractedVpk]:
    try:
        import py7zr
        from py7zr import exceptions as py7zr_exceptions
    except ImportError as e:
        raise ArchiveError('The py7zr package is missing: pip install py7zr') from e
    try:
        with py7zr.SevenZipFile(archive, mode='r') as szf:
            names = _7z_vpk_names(szf.list())
            for name in names:
                safe_member_path(dest, name)
            if names:
                szf.extract(path=dest, targets=names)
    except _py7zr_errors(py7zr_exceptions) as e:
        raise ArchiveError(f'Cannot extract {archive.name}: {e}') from e
    return collect_vpks(dest)


def _copy_single_vpk(archive: Path, dest: Path) -> list[ExtractedVpk]:
    target = dest / archive.name
    try:
        shutil.copyfile(archive, target)
        size = target.stat().st_size
    except OSError as e:
        raise ArchiveError(f'Cannot read {archive.name}: {e}') from e
    return [ExtractedVpk(archive.name, target, size)]


_EXTRACTORS = {
    '.zip': _extract_zip,
    '.rar': _extract_rar,
    '.7z': _extract_7z,
    '.vpk': _copy_single_vpk,
}


def extract_vpks(archive: Path, dest: Path) -> list[ExtractedVpk]:
    """Extract only the .vpk files of `archive` into `dest` (an empty temp folder)."""
    if not archive.is_file():
        raise ArchiveError(f'Archive not found: {archive}')
    if not dest.is_dir():
        raise ArchiveError(f'Extraction folder does not exist: {dest}')
    extractor = _EXTRACTORS.get(archive.suffix.lower())
    if extractor is None:
        raise ArchiveError(f'Unsupported file type: {archive.suffix or archive.name}')
    return sorted(extractor(archive, dest), key=lambda vpk: vpk.relative.lower())
