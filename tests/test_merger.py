import zipfile

import pytest

from dlmm import installer, manifest, merger, toggler
from dlmm.errors import InstallError
from dlmm.installer import InstallRequest

SKIN = 'engineer_as_mcginnis_red_df21b'
VOICE = 'voicelines_engineer_as_mcginnis_d5e01'


def _zip(path, data: bytes):
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('pak01_dir.vpk', data)
    return path


def test_merge_two_enabled_mods(install_mod, fake_game):
    skin = install_mod(SKIN, paths=('models/m.vmdl_c',))
    voice = install_mod(VOICE, paths=('sounds/v.vsnd_c',))
    record = merger.merge(skin, [voice], 'McGinnis Engineer')
    assert record.name == 'McGinnis Engineer' and len(record.files) == 2
    assert [m.mod_id for m in manifest.load_mods()] == [skin.mod_id]
    assert all((fake_game.addons / f.slot).is_file() for f in record.files)


def test_merge_into_disabled_target_parks_files(install_mod):
    target = toggler.disable(install_mod('Target', paths=('models/t.vmdl_c',)))
    source = install_mod('Source', paths=('models/s.vmdl_c',))
    record = merger.merge(target, [source])
    assert not record.enabled and record.name == 'Target'
    slots = [f.slot for f in record.files]
    assert len(set(slots)) == 2
    assert all((record.parking / slot).is_file() for slot in slots)
    assert manifest.find_mod(source.mod_id) is None
    assert not source.parking.exists()


def test_merge_rolls_back_when_file_changed(install_mod, fake_game):
    target = install_mod('Target', paths=('models/t.vmdl_c',))
    source = install_mod('Source', paths=('models/s.vmdl_c',))
    (fake_game.addons / source.files[0].slot).write_bytes(b'edited')
    with pytest.raises(InstallError):
        merger.merge(target, [source])
    assert {m.mod_id for m in manifest.load_mods()} == {target.mod_id, source.mod_id}


def test_merge_rejects_self(install_mod):
    mod = install_mod('Solo')
    with pytest.raises(InstallError):
        merger.merge(mod, [mod])


def test_merge_candidates_rank_similar_names(install_mod):
    skin = install_mod(SKIN, paths=('models/m.vmdl_c',))
    install_mod('Rabbit Paige', paths=('models/p.vmdl_c',))
    voice = install_mod(VOICE, paths=('sounds/v.vsnd_c',))
    ranked = merger.merge_candidates(manifest.load_mods(), skin)
    assert ranked[0][0].mod_id == voice.mod_id and ranked[0][1] is True
    assert ranked[1][1] is False


def test_install_into_existing_mod(install_mod, tmp_path, fake_game, vpk_bytes):
    skin = install_mod('Skin', paths=('models/m.vmdl_c',))
    archive = _zip(tmp_path / 'voices.zip', vpk_bytes(['sounds/v.vsnd_c']))
    request = InstallRequest(archive, 'voices', into_mod_id=skin.mod_id)
    record = installer.install(request, fake_game, lambda vpks: vpks)
    assert record.mod_id == skin.mod_id and len(record.files) == 2
    assert len(manifest.load_mods()) == 1


def test_duplicate_callback_blocks_reinstall(install_mod, tmp_path, fake_game, vpk_bytes):
    data = vpk_bytes(['models/m.vmdl_c'])
    first = install_mod('Rabbit Paige', data=data)
    archive = _zip(tmp_path / 'again.zip', data)
    seen = []

    def _decline(existing):
        seen.append(existing.mod_id)
        return False

    result = installer.install(InstallRequest(archive, 'again'), fake_game, lambda vpks: vpks, _decline)
    assert result is None and seen == [first.mod_id]
    assert len(manifest.load_mods()) == 1
