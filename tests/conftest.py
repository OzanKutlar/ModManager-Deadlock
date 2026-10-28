import pytest

from dlmm.game_paths import GamePaths

SAMPLE_GAMEINFO = (
    '"GameInfo"\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n'
    '\t\t\tMod                 citadel\n'
    '\t\t\tWrite               citadel\n'
    '\t\t\tGame                citadel\n'
    '\t\t\tWrite               core\n'
    '\t\t\tMod                 core\n'
    '\t\t\tGame                core\n'
    '\t\t}\n\t}\n}\n'
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / 'dlmm-home'
    monkeypatch.setenv('DLMM_HOME', str(home))
    return home


@pytest.fixture
def sample_gameinfo() -> str:
    return SAMPLE_GAMEINFO


@pytest.fixture
def fake_game(tmp_path) -> GamePaths:
    citadel = tmp_path / 'Deadlock' / 'game' / 'citadel'
    citadel.mkdir(parents=True)
    (citadel / 'gameinfo.gi').write_text(SAMPLE_GAMEINFO, encoding='utf-8', newline='')
    return GamePaths(citadel)
