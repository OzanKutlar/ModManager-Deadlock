"""Interactive pickers: a prompt_toolkit pointer menu with a numbered Rich fallback.

Generalised from book_picker.py so one UI serves archives, mods, backups and menus.
Lists can offer sort orders: s cycles them, r reverses them, and pinned items never move.
"""

import sys
from dataclasses import dataclass
from typing import Any, Callable

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
MAX_SORT_PRESSES = 50
VISIBLE_ROWS = 15
PAGE_JUMP = 10
FOOTER = '↑/↓ move · PgUp/PgDn jump · Enter select · Esc cancel'
SORT_KEYS_HINT = 's sort · r reverse'
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
    pinned: bool = False


@dataclass(frozen=True)
class SortOption:
    """A named order for a list; key=None keeps the order the items were given in."""
    label: str
    key: Callable[[PickItem], Any] | None = None


@dataclass(frozen=True)
class SortChoice:
    label: str
    reverse: bool = False


class MenuUnavailable(RuntimeError):
    pass


def apply_sort(items: list[PickItem], option: SortOption, reverse: bool = False) -> list[PickItem]:
    """Reorder the unpinned items; pinned items keep their positions."""
    movable = [item for item in items if not item.pinned]
    if option.key is not None:
        movable = sorted(movable, key=option.key)
    if reverse:
        movable.reverse()
    ordered = iter(movable)
    return [item if item.pinned else next(ordered) for item in items]


class _Sorter:
    """The items in their current order plus the active sort."""

    def __init__(self, items: list[PickItem], sorts=None, choice: SortChoice | None = None):
        self.base = list(items)
        self.sorts = tuple(sorts or ())
        labels = [s.label for s in self.sorts]
        known = isinstance(choice, SortChoice) and choice.label in labels
        self.index = labels.index(choice.label) if known else 0
        self.reverse = bool(choice.reverse) if known else False
        self.items = self._ordered()

    @property
    def enabled(self) -> bool:
        return bool(self.sorts)

    @property
    def choice(self) -> SortChoice | None:
        if not self.sorts:
            return None
        return SortChoice(self.sorts[self.index].label, self.reverse)

    @property
    def status(self) -> str:
        if not self.sorts:
            return ''
        suffix = ' (reversed)' if self.reverse else ''
        return f'sort: {self.sorts[self.index].label}{suffix}'

    @property
    def footer(self) -> str:
        return f'{self.status} · {SORT_KEYS_HINT} · {FOOTER}' if self.sorts else FOOTER

    def _ordered(self) -> list[PickItem]:
        if not self.sorts:
            return list(self.base)
        return apply_sort(self.base, self.sorts[self.index], self.reverse)

    def cycle(self) -> None:
        if not self.sorts:
            return
        self.index = (self.index + 1) % len(self.sorts)
        self.reverse = False
        self.items = self._ordered()

    def flip(self) -> None:
        if not self.sorts:
            return
        self.reverse = not self.reverse
        self.items = self._ordered()


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


def _position(items: list[PickItem], target: PickItem) -> int:
    return next((index for index, item in enumerate(items) if item is target), 0)


def _resort(state: _MenuState, sorter: _Sorter, action) -> None:
    """Apply a sort change and keep the pointer on the same item."""
    current = sorter.items[state.index]
    action()
    state.jump(_position(sorter.items, current))


def _render_row(item: PickItem, selected: bool) -> list:
    if selected:
        row = [('class:pointer', ' ❯ '), ('class:selected', item.label)]
    else:
        row = [('', '   '), ('class:item', item.label)]
    if item.hint:
        row.append(('class:hint', f'  {item.hint}'))
    row.append(('', '\n'))
    return row


def _render_menu(title: str, items: list[PickItem], state: _MenuState, footer: str = FOOTER) -> list:
    fragments = [('class:title', f' {title}\n')]
    scrolling = state.count > VISIBLE_ROWS
    end = min(state.count, state.top + VISIBLE_ROWS)
    if scrolling:
        fragments.append(('class:dim', '   ↑ more\n' if state.top > 0 else '\n'))
    for idx in range(state.top, end):
        fragments.extend(_render_row(items[idx], idx == state.index))
    if scrolling:
        fragments.append(('class:dim', '   ↓ more\n' if end < state.count else '\n'))
    fragments.append(('class:dim', f' {state.index + 1}/{state.count} · {footer}'))
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


def _bind_sorting(kb, state: _MenuState, sorter: _Sorter) -> None:
    if not sorter.enabled:
        return

    @kb.add('s')
    def _sort(_event):
        _resort(state, sorter, sorter.cycle)

    @kb.add('r')
    def _reverse(_event):
        _resort(state, sorter, sorter.flip)


def _select_with_prompt_toolkit(title: str, sorter: _Sorter, default_index: int) -> int | None:
    if not HAS_PROMPT_TOOLKIT or not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise MenuUnavailable('interactive terminal unavailable')
    state = _MenuState(len(sorter.items), default_index)
    kb = KeyBindings()
    _bind_navigation(kb, state)
    _bind_actions(kb, state)
    _bind_sorting(kb, state, sorter)
    control = FormattedTextControl(
        lambda: _render_menu(title, sorter.items, state, sorter.footer), focusable=True, show_cursor=False
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


def _print_numbered(title: str, sorter: _Sorter) -> None:
    rule(escape(title))
    for idx, item in enumerate(sorter.items, start=1):
        hint = f'  [dim]{escape(item.hint)}[/dim]' if item.hint else ''
        console.print(f'  [cyan]{idx:2d}.[/cyan] {escape(item.label)}{hint}')
    if sorter.enabled:
        console.print(f'  [dim]{escape(sorter.status)} · {SORT_KEYS_HINT}[/dim]')


def _select_numbered(title: str, sorter: _Sorter) -> int | None:
    _print_numbered(title, sorter)
    keys = ', s sort, r reverse' if sorter.enabled else ''
    prompt = f'\n[bold]Select # (q to quit{keys}): [/bold]'
    count = len(sorter.items)
    for _ in range(MAX_NUMBERED_ATTEMPTS + MAX_SORT_PRESSES):
        try:
            answer = console.input(prompt).strip().lower()
        except (KeyboardInterrupt, EOFError):
            return None
        if answer in ('q', 'quit', ''):
            return None
        if sorter.enabled and answer in ('s', 'r'):
            (sorter.cycle if answer == 's' else sorter.flip)()
            _print_numbered(title, sorter)
            continue
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer) - 1
        warn(f'Enter a number between 1 and {count}, or q to quit.')
    return None


def pick(title: str, items: list[PickItem], default_index: int = 0, sorts=None,
         sort_choice: SortChoice | None = None,
         on_sort: Callable[[SortChoice], None] | None = None) -> PickItem | None:
    """Returns the chosen item, or None when cancelled or there is nothing to pick.

    With `sorts`, s/r change the order; on_sort receives the new choice when it changed.
    """
    if not items:
        return None
    sorter = _Sorter(items, sorts, sort_choice)
    start = items[default_index] if 0 <= default_index < len(items) else items[0]
    initial = sorter.choice
    try:
        index = _select_with_prompt_toolkit(title, sorter, _position(sorter.items, start))
    except MenuUnavailable:
        index = _select_numbered(title, sorter)
    if on_sort is not None and sorter.choice != initial:
        on_sort(sorter.choice)
    if index is None or not 0 <= index < len(sorter.items):
        return None
    return sorter.items[index]


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
