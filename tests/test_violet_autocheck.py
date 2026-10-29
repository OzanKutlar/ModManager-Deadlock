import argparse
import zipfile

import pytest

from dlmm import cli, violet
from dlmm.config import get_setting
from dlmm.errors import VpkMergeError

STALE_MODEL = 'models/heroes_wip/bookworm/bookworm.vmdl_c'
RAISE = 'raise'


def _args(**overrides) -> argparse.Namespace:
    values = dict(game_dir=None, into=None, hero=None, share=False, no_violet_check=False)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def env(monkeypatch, fake_game):
    monkeypatch.setattr(cli.sys, 'platform', 'win32')
    (fake_game.citadel / 'pak01_dir.vpk').write_bytes(b'base')
    state = {'scans': 0, 'fixed': [], 'picks': [], 'result': violet.STALE, 'answer': cli.VIOLET_FIX_NOW}

    def fake_scan(mod, game):
        state['scans'] += 1
        if state['result'] == RAISE:
            raise VpkMergeError('boom')
        models = (STALE_MODEL,) if state['result'] == violet.STALE else ()
        return violet.ModCheck(mod, (violet.FileCheck(mod.files[0].slot, state['result'], models),))

    def fake_pick(title, items, default_index=0):
        state['picks'].append(title)
        return next(item for item in items if item.value == state['answer'])

    monkeypatch.setattr(violet, 'scan', fake_scan)
    monkeypatch.setattr(cli, 'pick', fake_pick)
    monkeypatch.setattr(cli, '_violet_fix', lambda mods, game: state['fixed'].extend(m.mod_id for m in mods))
    return state


def test_stale_install_fix_now(env, install_mod, fake_game):
    record = install_mod('Paige Old')
    cli._offer_violet_fix(record, fake_game, _args())
    assert env['scans'] == 1 and len(env['picks']) == 1
    assert env['fixed'] == [record.mod_id]


def test_not_now_leaves_mod(env, install_mod, fake_game):
    env['answer'] = cli.VIOLET_NOT_NOW
    record = install_mod('Paige Old')
    cli._offer_violet_fix(record, fake_game, _args())
    assert env['fixed'] == []
    assert get_setting(cli.VIOLET_AUTOCHECK_SETTING) is None


def test_never_is_remembered(env, install_mod, fake_game):
    env['answer'] = cli.VIOLET_NEVER
    record = install_mod('Paige Old')
    cli._offer_violet_fix(record, fake_game, _args())
    cli._offer_violet_fix(record, fake_game, _args())
    assert env['scans'] == 1
    assert get_setting(cli.VIOLET_AUTOCHECK_SETTING) is False


def test_current_mod_has_no_prompt(env, install_mod, fake_game):
    env['result'] = violet.CURRENT
    record = install_mod('Fresh Skin')
    cli._offer_violet_fix(record, fake_game, _args())
    assert env['scans'] == 1 and env['picks'] == [] and env['fixed'] == []


def test_scan_error_does_not_break_install(env, tmp_path, fake_game, monkeypatch, vpk_bytes):
    env['result'] = RAISE
    monkeypatch.setattr(cli, 'find_game', lambda _override=None: fake_game)
    archive = tmp_path / 'new.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('pak01_dir.vpk', vpk_bytes(['models/a.vmdl_c']))
    assert cli._install_flow(archive, 'New', _args()) == 0
    assert env['scans'] == 1 and env['picks'] == []


def test_flag_and_platform_skip_scan(env, install_mod, fake_game, monkeypatch):
    record = install_mod('Mod')
    cli._offer_violet_fix(record, fake_game, _args(no_violet_check=True))
    monkeypatch.setattr(cli.sys, 'platform', 'linux')
    cli._offer_violet_fix(record, fake_game, _args())
    assert env['scans'] == 0


def test_autocheck_command(env):
    assert cli.cmd_violet(argparse.Namespace(autocheck='off')) == 0
    assert get_setting(cli.VIOLET_AUTOCHECK_SETTING) is False
    assert cli.cmd_violet(argparse.Namespace(autocheck='on')) == 0
    assert get_setting(cli.VIOLET_AUTOCHECK_SETTING) is None
