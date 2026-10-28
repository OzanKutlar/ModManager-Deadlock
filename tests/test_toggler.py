import json

import pytest

from dlmm import installer, manifest, toggler
from dlmm.config import app_dir
from dlmm.errors import InstallError


def test_disable_enable_round_trip(install_mod, fake_game):
    record = install_mod('Haze Witch')
    slot = fake_game.addons / 'pak01_dir.vpk'
    data = slot.read_bytes()
    disabled = toggler.disable(record)
    assert not disabled.enabled and not slot.exists()
    assert (disabled.parking / 'pak01_dir.vpk').read_bytes() == data
    assert manifest.find_mod(record.mod_id).enabled is False
    result = toggler.enable(disabled)
    assert result.mod.enabled and slot.read_bytes() == data
    assert result.mod.files[0].slot == 'pak01_dir.vpk'
    assert not disabled.parking.exists()


def test_enable_reallocates_taken_slot(install_mod):
    first = install_mod('First')
    install_mod('Second', paths=('models/b.vmdl_c',))
    toggler.disable(first)
    third = install_mod('Third', paths=('models/c.vmdl_c',))
    assert third.files[0].slot == 'pak01_dir.vpk'
    result = toggler.enable(first)
    assert result.mod.files[0].slot == 'pak03_dir.vpk'


def test_enabling_group_member_disables_rival(install_mod):
    witch = install_mod('Haze Witch', group='Haze skins')
    pirate = install_mod('Haze Pirate', group='  haze   SKINS ', enabled=False)
    assert pirate.group == 'Haze skins'
    result = toggler.enable(pirate)
    assert [m.mod_id for m in result.disabled_others] == [witch.mod_id]
    assert manifest.find_mod(witch.mod_id).enabled is False
    assert manifest.find_mod(pirate.mod_id).enabled is True


def test_enable_on_enabled_mod_still_enforces_group(install_mod):
    first = install_mod('Haze Witch', group='Haze skins')
    second = install_mod('Haze Pirate', paths=('models/p.vmdl_c',), group='Haze skins')
    result = toggler.enable(second)
    assert [m.mod_id for m in result.disabled_others] == [first.mod_id]


def test_changed_file_is_not_moved(install_mod, fake_game):
    record = install_mod('Mod')
    slot = fake_game.addons / 'pak01_dir.vpk'
    slot.write_bytes(b'user edit')
    with pytest.raises(InstallError):
        toggler.disable(record)
    assert slot.read_bytes() == b'user edit'
    assert manifest.find_mod(record.mod_id).enabled is True


def test_disabled_install_is_parked_and_uninstallable(install_mod, fake_game):
    record = install_mod('Parked', enabled=False)
    assert not (fake_game.addons / 'pak01_dir.vpk').exists()
    parked = record.parking / record.files[0].slot
    assert parked.is_file()
    result = installer.uninstall(record)
    assert result.removed == (record.files[0].slot,)
    assert not parked.exists()
    assert manifest.load_mods() == []


def test_old_manifest_loads_with_defaults(tmp_path):
    entry = {
        'mod_id': 'abc', 'name': 'Old', 'source': '', 'addons_dir': str(tmp_path), 'installed': 1.0,
        'files': [{'slot': 'pak01_dir.vpk', 'original': 'pak01_dir.vpk', 'sha256': 'x', 'size': 1}],
    }
    (app_dir() / 'installed.json').write_text(json.dumps({'version': 1, 'mods': [entry]}), encoding='utf-8')
    mods = manifest.load_mods()
    assert len(mods) == 1
    assert mods[0].enabled is True and mods[0].group is None
