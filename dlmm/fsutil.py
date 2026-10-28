"""Small filesystem helpers: hashing, atomic writes, read-only handling, formatting."""

import hashlib
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

HASH_CHUNK = 1 << 20
MAX_HASH_BYTES = 64 * 1024 ** 3
KIB = 1024
SECONDS_PER_DAY = 86_400


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for _ in range(MAX_HASH_BYTES // HASH_CHUNK + 1):
            chunk = handle.read(HASH_CHUNK)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    raise OSError(f'{path} is too large to hash')


def is_readonly(path: Path) -> bool:
    return path.exists() and not os.access(path, os.W_OK)


def set_writable(path: Path, writable: bool) -> None:
    mode = path.stat().st_mode
    new_mode = (mode | stat.S_IWRITE) if writable else (mode & ~stat.S_IWRITE)
    os.chmod(path, new_mode)


def discard_file(path: Path) -> None:
    """Remove a temporary file; a file that is already gone is fine."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _temp_sibling(target: Path) -> Path:
    fd, tmp = tempfile.mkstemp(prefix=f'.{target.name}.', suffix='.tmp', dir=str(target.parent))
    os.close(fd)
    return Path(tmp)


def atomic_write_text(target: Path, text: str) -> None:
    """Write text via temp file + rename, so a crash never leaves a half-written file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(target)
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        os.replace(tmp, target)
    except BaseException:
        discard_file(tmp)
        raise


def copy_atomic(source: Path, target: Path) -> None:
    """Copy a file via temp file + rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(target)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    except BaseException:
        discard_file(tmp)
        raise


def format_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < KIB:
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= KIB
    return f'{value:.1f} TB'


def format_age(stamp: float | None, now: float | None = None) -> str:
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or stamp <= 0:
        return ''
    current = time.time() if now is None else now
    seconds = max(0.0, current - stamp)
    if seconds < 60:
        return 'just now'
    if seconds < 3600:
        return f'{int(seconds // 60)}m ago'
    if seconds < SECONDS_PER_DAY:
        return f'{int(seconds // 3600)}h ago'
    if seconds < 30 * SECONDS_PER_DAY:
        return f'{int(seconds // SECONDS_PER_DAY)}d ago'
    return time.strftime('%Y-%m-%d', time.localtime(stamp))
