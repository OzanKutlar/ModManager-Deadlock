import struct
import zipfile

import pytest

from dlmm import installer
from dlmm.game_paths import GamePaths
from dlmm.installer import InstallRequest

# Pre-2026-09-29 layout (Mod/Write paths present).
SAMPLE_GAMEINFO = (
    '"GameInfo"\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n'
    '\t\t\tMod                 citadel\n'
    '\t\t\tWrite               citadel\n'
    '\t\t\tGame                citadel\n'
    '\t\t\tWrite               core\n'
    '\t\t\tMod                 core\n'
    '\t\t\tGame                core\n'
    '\t\t}\n\t}\n}\n'
)

# Post-2026-09-29 stock layout (no Mod/Write paths).
NEW_STOCK_GAMEINFO = (
    '"GameInfo"\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n'
    '\t\t\tGame                citadel\n'
    '\t\t\tGame                core\n'
    '\t\t}\n\t}\n}\n'
)

NUL = b'\x00'
VPK_ENTRY = struct.pack('<IHHIIH', 0, 0, 0x7FFF, 0, 0, 0xFFFF)


def build_vpk(paths: list[str]) -> bytes:
    """A minimal valid VPK v2 whose directory tree lists `paths` (no file data)."""
    by_ext: dict[str, dict[str, list[str]]] = {}
    for path in paths:
        directory, _sep, filename = path.rpartition('/')
        name, dot, ext = filename.rpartition('.')
        if not dot:
            name, ext = filename, ' '
        by_ext.setdefault(ext, {}).setdefault(directory or ' ', []).append(name)
    tree = bytearray()
    for ext, directories in by_ext.items():
        tree += ext.encode() + NUL
        for directory, names in directories.items():
            tree += directory.encode() + NUL
            for name in names:
                tree += name.encode() + NUL + VPK_ENTRY
            tree += NUL
        tree += NUL
    tree += NUL
    header = struct.pack('<III', 0x55AA1234, 2, len(tree)) + struct.pack('<IIII', 0, 0, 0, 0)
    return header + bytes(tree)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / 'dlmm-home'
    monkeypatch.setenv('DLMM_HOME', str(home))
    return home


@pytest.fixture
def sample_gameinfo() -> str:
    return SAMPLE_GAMEINFO


@pytest.fixture
def new_stock_gameinfo() -> str:
    return NEW_STOCK_GAMEINFO


@pytest.fixture
def vpk_bytes():
    return build_vpk


@pytest.fixture
def fake_game(tmp_path) -> GamePaths:
    citadel = tmp_path / 'Deadlock' / 'game' / 'citadel'
    citadel.mkdir(parents=True)
    (citadel / 'gameinfo.gi').write_text(SAMPLE_GAMEINFO, encoding='utf-8', newline='')
    return GamePaths(citadel)


@pytest.fixture
def install_mod(tmp_path, fake_game):
    """Install a one-VPK mod listing `paths`; returns the manifest record."""
    def _install(name, paths=('models/a.vmdl_c',), group=None, enabled=True, data=None):
        archive = tmp_path / f'{name}.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('pak01_dir.vpk', data if data is not None else build_vpk(list(paths)))
        request = InstallRequest(archive, name, group=group, enabled=enabled)
        return installer.install(request, fake_game, lambda vpks: vpks)
    return _install
