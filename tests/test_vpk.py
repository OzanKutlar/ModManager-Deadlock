import pytest

from dlmm import vpk
from dlmm.errors import VpkError


def test_lists_files(vpk_bytes, tmp_path):
    path = tmp_path / 'pak01_dir.vpk'
    path.write_bytes(vpk_bytes(['models/heroes/haze/Body.vmdl_c', 'readme', 'scripts/a.txt']))
    assert vpk.list_files(path) == frozenset({'models/heroes/haze/body.vmdl_c', 'readme', 'scripts/a.txt'})


def test_rejects_bad_signature(tmp_path):
    path = tmp_path / 'bad.vpk'
    path.write_bytes(bytes(40))
    with pytest.raises(VpkError):
        vpk.list_files(path)


def test_rejects_truncated_tree(vpk_bytes, tmp_path):
    path = tmp_path / 'short.vpk'
    path.write_bytes(vpk_bytes(['models/a.vmdl_c'])[:-3])
    with pytest.raises(VpkError):
        vpk.list_files(path)


def test_rejects_tiny_file(tmp_path):
    path = tmp_path / 'tiny.vpk'
    path.write_bytes(b'abc')
    with pytest.raises(VpkError):
        vpk.list_files(path)
