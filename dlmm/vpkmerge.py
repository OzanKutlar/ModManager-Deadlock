"""Run the official vpkmerge v0.22.1 by Slush97 (MIT): pinned download, SHA-256 check before use.

Used for the Violet attachment fix (`attachments check` / `attachments heal`) and for packing
several VPKs into one (`vpkmerge <out_dir.vpk> <in...>`). The check/heal workflow and output
parsing follow Deadlocker Mod Fixer by impyrobot (MIT).
"""

import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import app_dir
from .errors import VpkMergeError
from .fsutil import discard_file, sha256_file

VERSION = '0.22.1'
SHA256 = 'b4a5d0e3a3a1d4e627cb4979216807295351c7fb38e5a3aa6345b213a5c69275'
DOWNLOAD_URL = 'https://github.com/Slush97/vpkmerge/releases/download/v0.22.1/vpkmerge-windows-x86_64.exe'
EXE_NAME = 'vpkmerge.exe'
TOOLS_DIRNAME = 'tools'
MAX_DOWNLOAD_BYTES = 64 * 1024 ** 2
CHUNK = 1 << 20
DOWNLOAD_TIMEOUT = 60
RUN_TIMEOUT = 900
MAX_OUTPUT_LINES = 5_000
SUMMARY_RE = re.compile(r'(\d+) of \d+ mods? ship')
STALE_RE = re.compile(r'^\s+(\S+)\s+stale:')


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    stale: bool
    stale_models: tuple[str, ...]


# ----------------------------------------------------------------------
# Tool management
# ----------------------------------------------------------------------

def tool_path() -> Path:
    return app_dir() / TOOLS_DIRNAME / EXE_NAME


def _hash_ok(path: Path) -> bool:
    try:
        return sha256_file(path) == SHA256
    except OSError:
        return False


def tool_state() -> str:
    if sys.platform != 'win32':
        return 'not supported on this platform'
    path = tool_path()
    if not path.is_file():
        return 'not downloaded yet (downloads on first use)'
    return f'v{VERSION}, SHA-256 verified' if _hash_ok(path) else 'present but hash mismatch (will be replaced)'


def _stream(response, handle) -> None:
    written = 0
    for _ in range(MAX_DOWNLOAD_BYTES // CHUNK + 2):
        chunk = response.read(CHUNK)
        if not chunk:
            return
        written += len(chunk)
        if written > MAX_DOWNLOAD_BYTES:
            break
        handle.write(chunk)
    raise VpkMergeError('vpkmerge download is larger than expected; aborted.')


def _fetch(part: Path) -> None:
    request = urllib.request.Request(DOWNLOAD_URL, headers={'User-Agent': f'dlmm/{__version__}'})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, open(part, 'wb') as handle:
            if urllib.parse.urlparse(response.geturl()).scheme != 'https':
                raise VpkMergeError('vpkmerge download was redirected to a non-HTTPS URL; aborted.')
            _stream(response, handle)
    except (urllib.error.URLError, OSError) as e:
        raise VpkMergeError(f'Could not download vpkmerge: {e}') from e


def _download(target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise VpkMergeError(f'Cannot create {target.parent}: {e}') from e
    part = target.with_name(target.name + '.part')
    try:
        _fetch(part)
        if not _hash_ok(part):
            raise VpkMergeError('Downloaded vpkmerge failed the SHA-256 check; it was deleted and not run.')
        os.replace(part, target)
    except OSError as e:
        discard_file(part)
        raise VpkMergeError(f'Could not store vpkmerge: {e}') from e
    except BaseException:
        discard_file(part)
        raise


def ensure_tool() -> Path:
    """Path of a verified vpkmerge.exe, downloading the official build if needed."""
    if sys.platform != 'win32':
        raise VpkMergeError('vpkmerge features are only available on Windows (the pinned build is the Windows one).')
    path = tool_path()
    if path.is_file() and _hash_ok(path):
        return path
    if path.is_file():
        try:
            path.unlink()
        except OSError as e:
            raise VpkMergeError(f'Cannot remove the mismatching {path}: {e}') from e
    _download(path)
    return path


# ----------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------

def run(args: list[str], timeout: int = RUN_TIMEOUT) -> RunResult:
    """Run vpkmerge with an argument list (no shell); output lines are stdout + stderr."""
    if not args or not all(isinstance(a, str) for a in args):
        raise VpkMergeError('Invalid vpkmerge arguments.')
    exe = ensure_tool()
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        done = subprocess.run(
            [str(exe), *args], capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout, creationflags=flags,
        )
    except subprocess.TimeoutExpired as e:
        raise VpkMergeError(f'vpkmerge timed out after {timeout}s.') from e
    except OSError as e:
        raise VpkMergeError(f'Could not run vpkmerge: {e}') from e
    text = (done.stdout or '') + (done.stderr or '')
    lines = tuple(line.rstrip() for line in text.splitlines()[:MAX_OUTPUT_LINES])
    return RunResult(done.returncode, lines)


def _ok(result: RunResult, what: str) -> RunResult:
    if result.exit_code != 0:
        tail = ' / '.join(line for line in result.lines[-3:] if line)
        raise VpkMergeError(f'vpkmerge {what} failed (exit {result.exit_code}): {tail}')
    return result


def parse_check(lines) -> CheckResult:
    """Parse 'N of M mods ship stale model attachments' plus '  <model>  stale: ...' lines."""
    summary = next((line for line in lines if SUMMARY_RE.search(line)), None)
    if summary is None:
        raise VpkMergeError('Could not understand the vpkmerge check output.')
    stale_count = int(SUMMARY_RE.search(summary).group(1))
    models = tuple(m.group(1) for m in (STALE_RE.match(line) for line in lines) if m)
    return CheckResult(stale_count > 0, models)


def check(base: Path, vpk_path: Path) -> CheckResult:
    result = _ok(run(['attachments', 'check', '--base', str(base), str(vpk_path)]), 'check')
    return parse_check(result.lines)


def heal(base: Path, vpk_path: Path, output: Path) -> CheckResult:
    """Heal into `output` (never the input) and verify that the result is clean."""
    if Path(output).resolve() == Path(vpk_path).resolve():
        raise VpkMergeError('Heal output must differ from the input file.')
    _ok(run(['attachments', 'heal', '--base', str(base), str(vpk_path), '--output', str(output)]), 'heal')
    if not Path(output).is_file():
        raise VpkMergeError('vpkmerge heal produced no output file.')
    result = check(base, Path(output))
    if result.stale:
        raise VpkMergeError('The healed file is still stale; the original was left untouched.')
    return result


def merge_vpks(output: Path, inputs: list[Path], strict: bool = False) -> RunResult:
    """Merge `inputs` into `output`; later inputs win where paths collide."""
    if len(inputs) < 2:
        raise VpkMergeError('Merging needs at least two VPKs.')
    args = [str(output), *(str(p) for p in inputs), '--verbose']
    if strict:
        args.append('--strict')
    result = _ok(run(args), 'merge')
    if not Path(output).is_file():
        raise VpkMergeError('vpkmerge merge produced no output file.')
    return result
