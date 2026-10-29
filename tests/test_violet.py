from dlmm import backups, manifest, violet, vpkmerge


def test_parse_check_stale_and_clean():
    stale_output = [
        '1 of 1 mod ships stale model attachments',
        '  models/heroes_wip/bookworm/bookworm.vmdl_c  stale: attach_camera, attach_hitbox',
    ]
    res = vpkmerge.parse_check(stale_output)
    assert res.stale is True
    assert res.stale_models == ('models/heroes_wip/bookworm/bookworm.vmdl_c',)

    clean_output = ['0 of 1 mods ship stale model attachments']
    res_clean = vpkmerge.parse_check(clean_output)
    assert res_clean.stale is False
    assert res_clean.stale_models == ()


def test_violet_fix_and_undo_flow(install_mod, fake_game, monkeypatch, tmp_path):
    mod = install_mod('Paige Stale', paths=('models/heroes_wip/bookworm/bookworm.vmdl_c',))
    vpk_file = fake_game.addons / mod.files[0].slot
    original_bytes = vpk_file.read_bytes()

    # Mock vpkmerge tool availability
    monkeypatch.setattr(vpkmerge, 'ensure_tool', lambda: tmp_path / 'vpkmerge.exe')

    # Mock check -> stale
    check_count = 0
    def mock_check(base, path):
        nonlocal check_count
        check_count += 1
        # First check is stale, verification check on healed is clean
        if check_count == 1:
            return vpkmerge.CheckResult(True, ('models/heroes_wip/bookworm/bookworm.vmdl_c',))
        return vpkmerge.CheckResult(False, ())

    # Mock heal
    def mock_heal(base, path, out):
        out.write_bytes(original_bytes + b'_HEALED')
        return vpkmerge.CheckResult(False, ())

    monkeypatch.setattr(vpkmerge, 'check', mock_check)
    monkeypatch.setattr(vpkmerge, 'heal', mock_heal)

    res = violet.fix(mod, fake_game)
    assert res.fixed == (mod.files[0].slot,)
    assert vpk_file.read_bytes() == original_bytes + b'_HEALED'

    # Manifest hash should match new healed file
    refreshed = manifest.find_mod(mod.mod_id)
    assert refreshed.files[0].healed_from is not None

    # Undo Violet fix
    undone = violet.undo(refreshed)
    assert vpk_file.read_bytes() == original_bytes
    assert undone.files[0].healed_from is None
