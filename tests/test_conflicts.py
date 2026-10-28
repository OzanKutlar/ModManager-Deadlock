from dlmm import cli, conflicts, manifest, toggler
from dlmm.config import set_setting

SHARED = 'models/heroes/haze/body.vmdl_c'


def test_reports_overlap(install_mod):
    install_mod('Haze Witch', paths=(SHARED, 'materials/x.vmat_c'))
    install_mod('Haze Pirate', paths=(SHARED, 'materials/y.vmat_c'))
    report = conflicts.find_conflicts(manifest.load_mods())
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.count == 1 and conflict.sample == (SHARED,)


def test_no_overlap_no_conflict(install_mod):
    install_mod('A', paths=('models/a.vmdl_c',))
    install_mod('B', paths=('models/b.vmdl_c',))
    assert conflicts.find_conflicts(manifest.load_mods()).conflicts == ()


def test_group_switch_resolves_conflict(install_mod):
    install_mod('Haze Witch', paths=(SHARED,), group='Haze')
    pirate = install_mod('Haze Pirate', paths=(SHARED, 'models/extra.vmdl_c'), group='Haze')
    toggler.enable(pirate)
    assert conflicts.find_conflicts(manifest.load_mods()).conflicts == ()


def test_only_with_filters_pairs(install_mod):
    a = install_mod('A', paths=(SHARED,))
    install_mod('B', paths=(SHARED, 'models/b.vmdl_c'))
    c = install_mod('C', paths=('models/c.vmdl_c',))
    assert len(conflicts.find_conflicts(manifest.load_mods(), only_with=a).conflicts) == 1
    assert conflicts.find_conflicts(manifest.load_mods(), only_with=c).conflicts == ()


def test_unreadable_vpk_is_reported(install_mod):
    install_mod('Broken', data=b'not a vpk')
    report = conflicts.find_conflicts(manifest.load_mods())
    assert report.conflicts == ()
    assert len(report.unreadable) == 1


def test_is_ignorable():
    assert conflicts.is_ignorable('readme.txt')
    assert conflicts.is_ignorable('docs/license.md')
    assert conflicts.is_ignorable('preview.png')
    assert not conflicts.is_ignorable('models/a.vmdl_c')
    assert not conflicts.is_ignorable('soundevents/x.vsndevts_c')
    assert not conflicts.is_ignorable('root_resource.vmdl_c')


def test_readme_is_not_a_conflict(install_mod):
    install_mod('A', paths=('readme.txt', 'docs/readme.txt', 'info.txt', 'models/a.vmdl_c'))
    install_mod('B', paths=('readme.txt', 'docs/readme.txt', 'info.txt', 'models/b.vmdl_c'))
    assert conflicts.find_conflicts(manifest.load_mods()).conflicts == ()


def test_ignored_pair_is_skipped(install_mod):
    a = install_mod('A', paths=(SHARED,))
    b = install_mod('B', paths=(SHARED, 'models/b.vmdl_c'))
    ignored = {conflicts.pair_key(a, b)}
    assert conflicts.find_conflicts(manifest.load_mods(), None, ignored).conflicts == ()


def test_ignore_list_round_trip_and_reset(install_mod):
    a = install_mod('A', paths=(SHARED,))
    b = install_mod('B', paths=(SHARED, 'models/b.vmdl_c'))
    cli._ignore_pair(a, b)
    assert conflicts.pair_key(a, b) in cli._load_ignored()
    set_setting(cli.IGNORED_CONFLICTS_SETTING, None)
    assert cli._load_ignored() == set()


def test_find_duplicates(install_mod):
    first = install_mod('Rabbit Paige', paths=(SHARED,))
    second = install_mod('Rabbit Paige copy', paths=(SHARED,))
    install_mod('Other', paths=('models/o.vmdl_c',))
    pairs = conflicts.find_duplicates(manifest.load_mods())
    assert [(a.mod_id, b.mod_id) for a, b in pairs] == [(first.mod_id, second.mod_id)]
