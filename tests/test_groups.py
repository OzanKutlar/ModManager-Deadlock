import pytest

from dlmm import manifest, toggler
from dlmm.manifest import InstalledFile, InstalledMod


def _mod(mod_id: str, group=None, enabled=True) -> InstalledMod:
    return InstalledMod(
        mod_id=mod_id, name=mod_id, source='', addons_dir='/x', installed=1.0,
        files=(InstalledFile('pak01_dir.vpk', 'a.vpk', 'h', 1),), group=group, enabled=enabled,
    )


MODS = [_mod('a', 'Haze skins'), _mod('b', 'Lash skins'), _mod('c', 'haze SKINS'), _mod('d')]


def test_list_groups_dedupes_case_insensitively():
    assert manifest.list_groups(MODS) == ['Haze skins', 'Lash skins']


def test_canonical_group():
    assert manifest.canonical_group(MODS, '  HAZE   skins ') == 'Haze skins'
    assert manifest.canonical_group(MODS, 'Mo & Krill') == 'Mo & Krill'
    assert manifest.canonical_group(MODS, '   ') is None
    assert manifest.canonical_group(MODS, None) is None


def test_group_members():
    assert [m.mod_id for m in manifest.group_members(MODS, 'haze skins')] == ['a', 'c']
    assert manifest.group_members(MODS, None) == []


@pytest.mark.parametrize('name', ['Haze_Witch_Skin', 'HazeWitchSkin', 'hazewitch_v2', 'Haze Witch'])
def test_suggest_ranks_name_match_first(name):
    suggestions = toggler.suggest_groups(MODS, name, f'{name}.rar')
    assert suggestions[0] == ('Haze skins', True)
    assert ('Lash skins', False) in suggestions


def test_stop_words_do_not_match():
    suggestions = toggler.suggest_groups(MODS, 'Cool Skin Mod v2', 'cool_skin_mod.rar')
    assert all(not matched for _group, matched in suggestions)


def test_no_groups_means_no_suggestions():
    assert toggler.suggest_groups([_mod('x')], 'Haze Witch') == []
