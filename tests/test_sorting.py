import argparse

from dlmm import cli, picker, sorting
from dlmm.config import get_setting, set_setting
from dlmm.manifest import InstalledFile, InstalledMod
from dlmm.picker import PickItem


def _mod(name: str, installed: float, hero=None) -> InstalledMod:
    return InstalledMod(
        mod_id=name, name=name, source='', addons_dir='/x', installed=installed,
        files=(InstalledFile('pak01_dir.vpk', 'a.vpk', 'h', 1),), hero=hero,
    )


MODS = [_mod('beta', 1.0, 'Paige'), _mod('Alpha', 3.0, '(sound mod)'), _mod('gamma', 2.0, 'Haze'), _mod('delta', 4.0, 'Paige')]


def _items() -> list[PickItem]:
    return [PickItem(m.name, value=m) for m in MODS]


def test_sort_mods_orders():
    assert [m.name for m in sorting.sort_mods(MODS, 'character')] == ['gamma', 'beta', 'delta', 'Alpha']
    assert [m.name for m in sorting.sort_mods(MODS, 'time')] == ['delta', 'Alpha', 'gamma', 'beta']
    assert [m.name for m in sorting.sort_mods(MODS, 'name')] == ['Alpha', 'beta', 'delta', 'gamma']


def test_apply_sort_keeps_pinned_items():
    items = [PickItem('Top', pinned=True), *_items(), PickItem('Bottom', pinned=True)]
    ordered = picker.apply_sort(items, sorting.BY_NAME)
    assert [i.label for i in ordered] == ['Top', 'Alpha', 'beta', 'delta', 'gamma', 'Bottom']
    flipped = picker.apply_sort(items, sorting.BY_NAME, reverse=True)
    assert [i.label for i in flipped] == ['Top', 'gamma', 'delta', 'beta', 'Alpha', 'Bottom']


def test_resort_keeps_selection():
    sorter = picker._Sorter(_items(), sorting.MOD_SORTS, None)
    state = picker._MenuState(len(MODS))
    state.jump(2)
    selected = sorter.items[2]
    picker._resort(state, sorter, sorter.cycle)
    assert sorter.items[state.index] is selected
    picker._resort(state, sorter, sorter.flip)
    assert sorter.items[state.index] is selected


def test_choice_is_remembered(monkeypatch):
    def press_s(title, sorter, default_index):
        sorter.cycle()
        return 0

    monkeypatch.setattr(picker, '_select_with_prompt_toolkit', press_s)
    assert sorting.pick_sorted('t', _items()).value.name == 'delta'
    assert get_setting(sorting.MOD_SETTING) == {'label': sorting.BY_TIME.label, 'reverse': False}
    monkeypatch.setattr(picker, '_select_with_prompt_toolkit', lambda title, sorter, default_index: 0)
    assert sorting.pick_sorted('t', _items()).value.name == 'delta'


def test_corrupt_setting_falls_back(monkeypatch):
    set_setting(sorting.MOD_SETTING, 'nonsense')
    assert sorting.load_choice(sorting.MOD_SETTING) is None
    set_setting(sorting.MOD_SETTING, {'label': 'Unknown', 'reverse': False})
    monkeypatch.setattr(picker, '_select_with_prompt_toolkit', lambda title, sorter, default_index: 0)
    assert sorting.pick_sorted('t', _items()).value.name == 'gamma'


def test_numbered_fallback_sorts(monkeypatch):
    def unavailable(*_args):
        raise picker.MenuUnavailable('no tty')

    answers = iter(['s', '1'])
    monkeypatch.setattr(picker, '_select_with_prompt_toolkit', unavailable)
    monkeypatch.setattr(picker.console, 'input', lambda prompt='': next(answers))
    assert sorting.pick_sorted('t', _items()).value.name == 'delta'


def test_list_sort_name(install_mod, capsys):
    install_mod('Zed', paths=('models/z.vmdl_c',))
    install_mod('Abe', paths=('models/a.vmdl_c',))
    assert cli.cmd_list(argparse.Namespace(sort='name')) == 0
    out = capsys.readouterr().out
    assert out.index('Abe') < out.index('Zed')
