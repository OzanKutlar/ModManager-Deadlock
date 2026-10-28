import pytest

from dlmm import cli, protocol
from dlmm.config import get_setting

OUR_COMMAND = 'python.exe C:/tools/deadlock_mm.py handle %1'
FOREIGN_COMMAND = 'C:/Program Files/Deadlock Mod Manager/dmm.exe %1'


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(cli.sys, 'platform', 'win32')


@pytest.fixture
def fake_protocol(monkeypatch):
    state = {'command': None, 'register_calls': 0}

    def _register():
        state['register_calls'] += 1
        previous = state['command']
        state['command'] = OUR_COMMAND
        return previous

    monkeypatch.setattr(protocol, 'read_command', lambda: state['command'])
    monkeypatch.setattr(protocol, 'register', _register)
    return state


def _answer(monkeypatch, value: bool) -> list:
    asked = []

    def _confirm(question, default=False):
        asked.append(question)
        return value

    monkeypatch.setattr(cli, 'confirm', _confirm)
    return asked


def test_yes_registers(windows, fake_protocol, monkeypatch):
    asked = _answer(monkeypatch, True)
    cli._offer_protocol_takeover()
    assert asked == [cli.PROTOCOL_PROMPT]
    assert fake_protocol['register_calls'] == 1
    assert get_setting(cli.PROTOCOL_PROMPT_SETTING) is None


def test_yes_with_foreign_handler_registers(windows, fake_protocol, monkeypatch):
    fake_protocol['command'] = FOREIGN_COMMAND
    asked = _answer(monkeypatch, True)
    cli._offer_protocol_takeover()
    assert len(asked) == 1
    assert fake_protocol['register_calls'] == 1


def test_no_is_remembered(windows, fake_protocol, monkeypatch):
    asked = _answer(monkeypatch, False)
    cli._offer_protocol_takeover()
    cli._offer_protocol_takeover()
    assert len(asked) == 1
    assert fake_protocol['register_calls'] == 0
    assert get_setting(cli.PROTOCOL_PROMPT_SETTING) is True


def test_no_prompt_when_already_registered(windows, fake_protocol, monkeypatch):
    fake_protocol['command'] = OUR_COMMAND
    asked = _answer(monkeypatch, True)
    cli._offer_protocol_takeover()
    assert asked == []
    assert fake_protocol['register_calls'] == 0


def test_no_prompt_off_windows(fake_protocol, monkeypatch):
    monkeypatch.setattr(cli.sys, 'platform', 'linux')
    asked = _answer(monkeypatch, True)
    cli._offer_protocol_takeover()
    assert asked == []


def test_unregister_counts_as_no(windows, monkeypatch):
    monkeypatch.setattr(protocol, 'unregister', lambda: 'handler removed')
    assert cli.cmd_unregister(None) == 0
    assert get_setting(cli.PROTOCOL_PROMPT_SETTING) is True
