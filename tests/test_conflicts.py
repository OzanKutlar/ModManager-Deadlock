from dlmm import conflicts, manifest, toggler

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
