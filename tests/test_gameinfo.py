import pytest

from dlmm import backups, gameinfo
from dlmm.errors import GameInfoError


def _entries(text: str) -> list[list[str]]:
    start = text.index('SearchPaths')
    block = text[text.index('{', start) + 1:text.index('}', start)]
    return [line.split() for line in block.splitlines() if line.strip()]


def test_patch_inserts_addons_first(sample_gameinfo):
    patched = gameinfo.patch(sample_gameinfo)
    assert _entries(patched)[0] == ['Game', 'citadel/addons']
    assert gameinfo.is_patched(patched)
    assert not gameinfo.is_patched(sample_gameinfo)


def test_patch_is_idempotent(sample_gameinfo):
    once = gameinfo.patch(sample_gameinfo)
    assert gameinfo.patch(once) == once


def test_patch_moves_misplaced_entry(sample_gameinfo):
    core_line = '\t\t\tGame                core\n'
    misplaced = sample_gameinfo.replace(core_line, core_line + '\t\t\tGame citadel/addons\n')
    entries = _entries(gameinfo.patch(misplaced))
    assert entries[0] == ['Game', 'citadel/addons']
    assert sum(1 for e in entries if e[-1] == 'citadel/addons') == 1


def test_patch_preserves_crlf(sample_gameinfo):
    crlf = sample_gameinfo.replace('\n', '\r\n')
    patched = gameinfo.patch(crlf)
    assert '\n' not in patched.replace('\r\n', '')
    assert gameinfo.is_patched(patched)


def test_unpatch_round_trip(sample_gameinfo):
    assert gameinfo.unpatch(gameinfo.patch(sample_gameinfo)) == sample_gameinfo


def test_missing_block_raises():
    with pytest.raises(GameInfoError):
        gameinfo.patch('"GameInfo"\n{\n}\n')


def test_ensure_patched_backs_up_and_pins_original(tmp_path, sample_gameinfo):
    path = tmp_path / 'gameinfo.gi'
    path.write_text(sample_gameinfo, encoding='utf-8', newline='')
    assert gameinfo.ensure_patched(path) is True
    assert gameinfo.ensure_patched(path) is False
    entries = backups.list_backups()
    assert len(entries) == 1 and entries[0].pinned
    assert entries[0].stored_path.read_text(encoding='utf-8') == sample_gameinfo
