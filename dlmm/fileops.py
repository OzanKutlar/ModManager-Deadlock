"""Hash-checked file moves with rollback, shared by enable/disable and merges."""

import os
from pathlib import Path

from rich.markup import escape

from . import ui
from .errors import InstallError
from .fsutil import copy_atomic, sha256_file

MovePlan = list[tuple[Path, Path, str]]


def _copy_verify_unlink(source: Path, target: Path, expected_sha: str) -> None:
    """Fallback when a rename is impossible (e.g. different drives)."""
    copy_atomic(source, target)
    if sha256_file(target) != expected_sha:
        target.unlink()
        raise InstallError(f'Copy of {source.name} is corrupt; original left in place.')
    source.unlink()


def move_file(source: Path, target: Path, expected_sha: str) -> None:
    """Move one file after checking its hash; never overwrites."""
    if not source.is_file():
        raise InstallError(f'Missing file: {source}')
    if target.exists():
        raise InstallError(f'Refusing to overwrite existing file: {target}')
    if sha256_file(source) != expected_sha:
        raise InstallError(f'{source.name} changed since install; not moving it.')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:
        _copy_verify_unlink(source, target, expected_sha)


def undo_moves(done: MovePlan) -> None:
    for source, target, sha in reversed(done):
        try:
            move_file(target, source, sha)
        except (InstallError, OSError) as e:
            ui.warn(f'Could not move {escape(str(target))} back: {escape(str(e))}')


def move_all(plan: MovePlan) -> None:
    """Run every move or none: a failure moves the finished ones back."""
    done: MovePlan = []
    try:
        for step in plan:
            move_file(*step)
            done.append(step)
    except (InstallError, OSError) as e:
        undo_moves(done)
        raise InstallError(f'{e} (no files were changed)') from e
