"""Interactive pickers: a prompt_toolkit pointer menu with a numbered Rich fallback.

Generalised from book_picker.py so one UI serves archives, mods, backups and menus.
"""

import sys
from dataclasses import dataclass
from typing import Any

from rich.markup import escape

from .ui import console, rule, warn

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

MAX_NUMBERED_ATTEMPTS = 5
VISIBLE_ROWS = 15
PAGE_JUMP = 10
FOOTER = '↑/↓ move · PgUp/PgDn jump · Enter select · Esc cancel'
MENU_STYLE = {
    'title': 'bold ansicyan',
    'pointer': 'bold ansicyan',
    'selected': 'reverse bold',
    'item': '',
    'hint': 'ansibrightblack',
    'dim': 'ansibrightblack',
}


@dataclass(frozen=True)
class PickItem:
    label: str
    hint: str = ''
    value: Any = None


class MenuUnavailable(RuntimeError):
    pass


class _MenuState:
    def __init__(self, count: int, default_index: int = 0):
        self.count = count
        self.index = default_index if 0 <= default_index < count else 0
        self.top = 0
        self._scroll()

    def move(self, delta: int, wrap: bool = True) -> None:
        if wrap:
            self.index = (self.index + delta) % self.count
        else:
            self.index = max(0, min(self.count - 1, self.index + delta))
        self._scroll()

    def jump(self, index: int) -> None:
        self.index = max(0, min(self.count - 1, index))
        self._scroll()

    def _scroll(self) -> None:
        if self.index < self.top:
            self.top = self.index
        elif self.index >= self.top + VISIBLE_ROWS:
            self.top = self.index - VISIBLE_ROWS + 1


def _render_row(item: PickItem, selected: bool) -> list:
    if selected:
        row = [('class:pointer', ' ❯ '), ('class:selected', item.label)]
    else:
        row = [('', '   '), ('class:item', item.label)]
    if item.hint:
        row.append(('class:hint', f'  {item.hint}'))
    row.append(('', '\n'))
    return row


def _render_menu(title: str, items: list[PickItem], state: _MenuState) -> list:
    fragments = [('class:title', f' {title}\n')]
    scrolling = state.count > VISIBLE_ROWS
    end = min(state.count, state.top + VISIBLE_ROWS)
    if scrolling:
        fragments.append(('class:dim', '   ↑ more\n' if state.top > 0 else '\n'))
    for idx in range(state.top, end):
        fragments.extend(_render_row(items[idx], idx == state.index))
    if scrolling:
        fragments.append(('class:dim', '   ↓ more\n' if end < state.count else '\n'))
    fragments.append(('class:dim', f' {state.index + 1}/{state.count} · {FOOTER}'))
    return fragments


def _bind_navigation(kb, state: _MenuState) -> None:
    @kb.add('up')
    @kb.add('k')
    def _up(_event):
        state.move(-1)

    @kb.add('down')
    @kb.add('j')
    def _down(_event):
        state.move(1)

    @kb.add('pageup')
    def _page_up(_event):
        state.move(-PAGE_JUMP, wrap=False)

    @kb.add('pagedown')
    def _page_down(_event):
        state.move(PAGE_JUMP, wrap=False)

    @kb.add('home')
    def _home(_event):
        state.jump(0)

    @kb.add('end')
    def _end(_event):
        state.jump(state.count - 1)


def _bind_actions(kb, state: _MenuState) -> None:
    @kb.add('enter')
    def _select(event):
        event.app.exit(result=state.index)

    @kb.add('escape', eager=True)
    @kb.add('q')
    @kb.add('c-c')
    def _cancel(event):
        event.app.exit(result=None)


def _select_with_prompt_toolkit(title: str, items: list[PickItem], default_index: int) -> int | None:
    if not HAS_PROMPT_TOOLKIT or not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise MenuUnavailable('interactive terminal unavailable')
    state = _MenuState(len(items), default_index)
    kb = KeyBindings()
    _bind_navigation(kb, state)
    _bind_actions(kb, state)
    control = FormattedTextControl(
        lambda: _render_menu(title, items, state), focusable=True, show_cursor=False
    )
    app = Application(
        layout=Layout(HSplit([Window(content=control, dont_extend_height=True, always_hide_cursor=True)])),
        key_bindings=kb,
        style=Style.from_dict(MENU_STYLE),
        full_screen=False,
        mouse_support=False,
        erase_when_done=True,
    )
    try:
        with patch_stdout(raw=True):
            result = app.run()
    except Exception as e:  # prompt_toolkit raises varied errors on unusual terminals
        raise MenuUnavailable(str(e)) from e
    return result if isinstance(result, int) else None


def _select_numbered(title: str, items: list[PickItem]) -> int | None:
    rule(escape(title))
    for idx, item in enumerate(items, start=1):
        hint = f'  [dim]{escape(item.hint)}[/dim]' if item.hint else ''
        console.print(f'  [cyan]{idx:2d}.[/cyan] {escape(item.label)}{hint}')
    for _ in range(MAX_NUMBERED_ATTEMPTS):
        try:
            answer = console.input('\n[bold]Select # (q to quit): [/bold]').strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if answer.lower() in ('q', 'quit', ''):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return int(answer) - 1
        warn(f'Enter a number between 1 and {len(items)}, or q to quit.')
    return None


def pick(title: str, items: list[PickItem], default_index: int = 0) -> PickItem | None:
    """Returns the chosen item, or None when cancelled or there is nothing to pick."""
    if not items:
        return None
    try:
        index = _select_with_prompt_toolkit(title, items, default_index)
    except MenuUnavailable:
        index = _select_numbered(title, items)
    if index is None or not 0 <= index < len(items):
        return None
    return items[index]


def confirm(question: str, default: bool = False) -> bool:
    suffix = escape('[Y/n]' if default else '[y/N]')
    for _ in range(MAX_NUMBERED_ATTEMPTS):
        try:
            answer = console.input(f'[bold]{escape(question)}[/bold] {suffix} ').strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        warn('Please answer y or n.')
    return False


def pause(message: str = 'Press Enter to close...') -> None:
    try:
        console.input(f'[dim]{escape(message)}[/dim]')
    except (KeyboardInterrupt, EOFError):
        return
