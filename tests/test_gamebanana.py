import pytest

from dlmm.errors import LinkError
from dlmm.gamebanana import ModLink, parse_mod_link, safe_filename


@pytest.mark.parametrize('raw', [
    'deadlock-mod-manager:https://gamebanana.com/mmdl/1751564,Mod,693381',
    'deadlock-mod-manager:https://gamebanana.com/dl/1751564,Mod,693381',
    'deadlock-mod-manager:https://www.gamebanana.com/dl/1751564,Mod,693381',
])
def test_parse_valid_link(raw):
    assert parse_mod_link(raw) == ModLink(file_id=1751564, item_type='Mod', item_id=693381)


def test_parse_quoted_and_percent_encoded():
    link = parse_mod_link('"deadlock-mod-manager:https://gamebanana.com/mmdl/1861275%2CMod%2C670908"')
    assert link.item_id == 670908 and link.file_id == 1861275


@pytest.mark.parametrize('raw', [
    'deadlock-mod-manager:https://evil.example/mmdl/1,Mod,2',
    'deadlock-mod-manager:http://gamebanana.com/mmdl/1,Mod,2',
    'deadlock-mod-manager:https://gamebanana.com/mmdl/abc,Mod,2',
    'deadlock-mod-manager:https://gamebanana.com/mmdl/1,Mod,2&calc.exe',
    'deadlock-mod-manager:https://gamebanana.com.evil.example/mmdl/1,Mod,2',
    'deadlock-mod-manager:https://gamebanana.com.dl.evil.example/dl/1,Mod,2',
    'deadlock-mod-manager:https://gamebanana.com/files/1,Mod,2',
    'deadlock-mod-manager:http://gamebanana.com/dl/1,Mod,2',
    'https://gamebanana.com/mmdl/1,Mod,2',
    '',
])
def test_parse_rejects_bad_links(raw):
    with pytest.raises(LinkError):
        parse_mod_link(raw)


def test_parse_rejects_non_mod_type():
    with pytest.raises(LinkError):
        parse_mod_link('deadlock-mod-manager:https://gamebanana.com/mmdl/1,Sound,2')


def test_safe_filename():
    assert safe_filename('../../evil.rar', 'x') == 'evil.rar'
    assert safe_filename('a<b>:c.rar', 'x') == 'a_b__c.rar'
    assert safe_filename('', 'fallback.rar') == 'fallback.rar'
    assert safe_filename('CON.rar', 'x') == '_CON.rar'
