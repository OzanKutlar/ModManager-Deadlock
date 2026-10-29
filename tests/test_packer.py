import pytest
from dlmm import manifest, packer, vpkmerge


def test_pack_multi_vpk_and_undo(install_mod, fake_game, monkeypatch, tmp_path, vpk_bytes):
    # Install mod with 2 VPKs
    data1 = vpk_bytes(['models/heroes_wip/bookworm/head.vmdl_c'])
    data2 = vpk_bytes(['models/heroes_wip/bookworm/body.vmdl_c'])
    mod = install_mod('Multi Paige', paths=(), data=data1)
    (fake_game.addons / 'pak02_dir.vpk').write_bytes(data2)
    f2 = manifest.InstalledFile('pak02_dir.vpk', 'part2.vpk', 'h2', len(data2))
    manifest.replace_mod(manifest.InstalledMod(
        mod.mod_id, mod.name, mod.source, mod.addons_dir, mod.installed,
        files=(mod.files[0], f2), hero='Paige'
    ))
    current_mod = manifest.find_mod(mod.mod_id)

    # Mock vpkmerge
    monkeypatch.setattr(vpkmerge, 'ensure_tool', lambda: tmp_path / 'vpkmerge.exe')
    def mock_merge(out, inputs, strict=False):
        # Produce combined valid VPK with both files
        out.write_bytes(vpk_bytes([
            'models/heroes_wip/bookworm/head.vmdl_c',
            'models/heroes_wip/bookworm/body.vmdl_c'
        ]))
        return vpkmerge.RunResult(0, ())
    monkeypatch.setattr(vpkmerge, 'merge_vpks', mock_merge)

    packed = packer.pack(current_mod)
    assert len(packed.files) == 1
    assert len(packed.packed_from) == 2
    assert (fake_game.addons / packed.files[0].slot).is_file()
    assert not (fake_game.addons / 'pak02_dir.vpk').exists()

    # Undo pack
    unpacked = packer.undo_pack(packed)
    assert len(unpacked.files) == 2
    assert (fake_game.addons / 'pak01_dir.vpk').exists()
    assert (fake_game.addons / 'pak02_dir.vpk').exists()
