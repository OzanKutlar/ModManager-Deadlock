import pytest

from dlmm import backups, gameinfo
from dlmm.errors import GameInfoError

EXPECTED_NEW_LAYOUT = [
    ['Game', 'citadel/addons'],
    ['Game', 'citadel'],
    ['Game', 'core'],
    ['Mod', 'citadel'],
    ['Write', 'citadel'],
    ['Mod', 'core'],
    ['Write', 'core'],
]


def _entries(text: str) -> list[list[str]]:
    start = text.index('SearchPaths')
    block = text[text.index('{', start) + 1:text.index('}', start)]
    return [line.split() for line in block.splitlines() if line.strip()]


def test_patch_inserts_addons_first(sample_gameinfo):
    patched = gameinfo.patch(sample_gameinfo)
    assert _entries(patched)[0] == ['Game', 'citadel/addons']
    assert gameinfo.is_patched(patched)
    assert not gameinfo.is_patched(sample_gameinfo)


def test_patch_old_layout_adds_no_duplicates(sample_gameinfo):
    entries = _entries(gameinfo.patch(sample_gameinfo))
    assert len(entries) == 7


def test_patch_is_idempotent(sample_gameinfo, new_stock_gameinfo):
    for text in (sample_gameinfo, new_stock_gameinfo):
        once = gameinfo.patch(text)
        assert gameinfo.patch(once) == once


def test_patch_new_layout_adds_mod_write_paths(new_stock_gameinfo):
    patched = gameinfo.patch(new_stock_gameinfo)
    assert _entries(patched) == EXPECTED_NEW_LAYOUT
    assert gameinfo.patch_state(patched) == gameinfo.PATCHED
    assert patched.endswith('\t\t}\n\t}\n}\n')


def test_repairs_incomplete_patch_from_old_version(new_stock_gameinfo):
    broken = new_stock_gameinfo.replace(
        '\t\t{\n\t\t\tGame                citadel\n',
        '\t\t{\n\t\t\tGame\t\t\t\tcitadel/addons\n\t\t\tGame                citadel\n',
    )
    assert gameinfo.patch_state(broken) == gameinfo.INCOMPLETE
    assert not gameinfo.is_patched(broken)
    assert _entries(gameinfo.patch(broken)) == EXPECTED_NEW_LAYOUT


def test_patch_state_unpatched(new_stock_gameinfo):
    assert gameinfo.patch_state(new_stock_gameinfo) == gameinfo.UNPATCHED


def test_patch_moves_misplaced_entry(sample_gameinfo):
    core_line = '\t\t\tGame                core\n'
    misplaced = sample_gameinfo.replace(core_line, core_line + '\t\t\tGame citadel/addons\n')
    entries = _entries(gameinfo.patch(misplaced))
    assert entries[0] == ['Game', 'citadel/addons']
    assert sum(1 for e in entries if e[-1] == 'citadel/addons') == 1


def test_patch_preserves_crlf(new_stock_gameinfo):
    crlf = new_stock_gameinfo.replace('\n', '\r\n')
    patched = gameinfo.patch(crlf)
    assert '\n' not in patched.replace('\r\n', '')
    assert gameinfo.is_patched(patched)


def test_unpatch_round_trip(sample_gameinfo):
    assert gameinfo.unpatch(gameinfo.patch(sample_gameinfo)) == sample_gameinfo


def test_base_layout_ignores_managed_lines(sample_gameinfo, new_stock_gameinfo):
    assert gameinfo.base_layout(gameinfo.patch(new_stock_gameinfo)) == gameinfo.base_layout(new_stock_gameinfo)
    assert gameinfo.base_layout(sample_gameinfo) == gameinfo.base_layout(new_stock_gameinfo)
    changed = new_stock_gameinfo.replace('"GameInfo"', '"GameInfo" // new build')
    assert gameinfo.base_layout(changed) != gameinfo.base_layout(new_stock_gameinfo)


def test_missing_block_raises():
    with pytest.raises(GameInfoError):
        gameinfo.patch('"GameInfo"\n{\n}\n')


def test_ensure_patched_backs_up_and_pins_original(tmp_path, new_stock_gameinfo):
    path = tmp_path / 'gameinfo.gi'
    path.write_text(new_stock_gameinfo, encoding='utf-8', newline='')
    assert gameinfo.ensure_patched(path) is True
    assert gameinfo.ensure_patched(path) is False
    entries = backups.list_backups()
    assert len(entries) == 1 and entries[0].pinned
    assert entries[0].stored_path.read_text(encoding='utf-8') == new_stock_gameinfo
