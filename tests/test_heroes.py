from dlmm import heroes


def test_describe_detects_codenames():
    assert heroes.describe(['models/heroes_wip/bookworm/bookworm.vmdl_c']) == 'Paige'
    assert heroes.describe(['models/heroes/engineer/engineer.vmdl_c']) == 'McGinnis'
    assert heroes.describe(['materials/models/heroes_staging/yamato_v2/body.vmat_c']) == 'Yamato'


def test_describe_detects_hero_cards():
    assert heroes.describe(['panorama/images/heroes/inferno_card_psd.vtex_c']) == 'Infernus'


def test_describe_detects_ability_assets():
    assert heroes.describe(['particles/abilities/lash/lash_jump.vpcf_c']) == 'Lash'


def test_describe_multi_hero_pack():
    paths = [
        'particles/abilities/lash/a.vpcf_c',
        'particles/abilities/haze/b.vpcf_c',
        'particles/abilities/yamato/c.vpcf_c',
    ]
    assert heroes.describe(paths) == heroes.MULTI_HERO


def test_describe_fallback_type():
    assert heroes.describe(['sounds/weapons/fire.vsnd_c']) == '(sound mod)'
    assert heroes.describe(['panorama/layout/custom_hud.xml']) == '(UI mod)'
    assert heroes.describe(['unknown_folder/asset.bin']) == heroes.OTHER


def test_canonical_lookups():
    assert heroes.canonical('bookworm') == 'Paige'
    assert heroes.canonical('PAIGE') == 'Paige'
    assert heroes.canonical('lady geist') == 'Lady Geist'
    assert heroes.canonical('unknown_hero_xyz') is None
