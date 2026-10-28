import zipfile

import pytest

from dlmm import backups, gameinfo, installer, manifest
from dlmm.archives import extract_vpks, safe_member_path
from dlmm.errors import ArchiveError
from dlmm.installer import InstallRequest


def _make_zip(path, members: dict):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_allocate_slots_skips_used(tmp_path):
    (tmp_path / 'pak01_dir.vpk').write_bytes(b'x')
    (tmp_path / 'pak03_dir.vpk').write_bytes(b'x')
    assert installer.allocate_slots(tmp_path, 2) == ['pak02_dir.vpk', 'pak04_dir.vpk']


@pytest.mark.parametrize('name', ['../evil.vpk', '/abs/evil.vpk', 'C:/evil.vpk', 'a/../../evil.vpk', 'x.vpk:ads'])
def test_safe_member_path_rejects(tmp_path, name):
    with pytest.raises(ArchiveError):
        safe_member_path(tmp_path, name)


def test_zip_slip_member_is_rejected(tmp_path):
    archive = _make_zip(tmp_path / 'bad.zip', {'../../evil.vpk': b'x'})
    dest = tmp_path / 'out'
    dest.mkdir()
    with pytest.raises(ArchiveError):
        extract_vpks(archive, dest)
    assert not (tmp_path / 'evil.vpk').exists()


def test_install_uninstall_rollback_round_trip(tmp_path, fake_game):
    archive = _make_zip(tmp_path / 'Cool Skin.zip', {
        'Variant A/pak01_dir.vpk': b'A' * 10,
        'Variant B/pak01_dir.vpk': b'B' * 10,
        'readme.txt': b'not copied',
    })
    record = installer.install(InstallRequest(archive, 'Cool Skin'), fake_game, lambda vpks: [vpks[1]])
    installed = fake_game.addons / 'pak01_dir.vpk'
    assert record is not None and installed.read_bytes() == b'B' * 10
    assert gameinfo.is_patched(gameinfo.read_gameinfo(fake_game.gameinfo))
    assert [m.mod_id for m in manifest.load_mods()] == [record.mod_id]

    result = installer.uninstall(record)
    assert result.removed == ('pak01_dir.vpk',)
    assert not installed.exists()
    assert manifest.load_mods() == []

    vpk_backup = next(e for e in backups.list_backups() if e.stored_file == 'pak01_dir.vpk')
    backups.restore_backup(vpk_backup)
    assert installed.read_bytes() == b'B' * 10


def test_uninstall_leaves_modified_file(tmp_path, fake_game):
    archive = _make_zip(tmp_path / 'mod.zip', {'pak01_dir.vpk': b'orig'})
    record = installer.install(InstallRequest(archive, 'Mod'), fake_game, lambda v: v)
    (fake_game.addons / 'pak01_dir.vpk').write_bytes(b'user changed')
    result = installer.uninstall(record)
    assert result.removed == ()
    assert (fake_game.addons / 'pak01_dir.vpk').exists()


def test_unmanaged_vpks(tmp_path, fake_game):
    fake_game.addons.mkdir()
    (fake_game.addons / 'pak05_dir.vpk').write_bytes(b'manual')
    assert installer.unmanaged_vpks(fake_game.addons, []) == ['pak05_dir.vpk']
