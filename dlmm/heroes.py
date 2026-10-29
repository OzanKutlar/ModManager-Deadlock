"""Name the Deadlock character a mod is for, from the file paths inside its VPKs.

Ported from Deadlocker Mod Fixer by impyrobot (MIT): codename table, display names and the
path-voting heuristics. Update both tables when Valve adds heroes.
"""

import re
from collections import Counter

CODENAMES = {
    'archer': 'Grey Talon', 'orion': 'Grey Talon', 'astro': 'Holliday', 'atlas': 'Abrams',
    'bookworm': 'Paige', 'chessmaster': 'Solomon', 'chrono': 'Paradox', 'digger': 'Mo & Krill',
    'engineer': 'McGinnis', 'familiar': 'Rem', 'fencer': 'Apollo', 'forge': 'McGinnis',
    'frank': 'Victor', 'ghost': 'Lady Geist', 'gigawatt': 'Seven', 'hornet': 'Vindicta',
    'inferno': 'Infernus', 'magician': 'Sinclair', 'nano': 'Calico', 'necro': 'Graves',
    'priest': 'Venator', 'punkgoat': 'Billy', 'synth': 'Pocket', 'tengu': 'Ivy',
    'unicorn': 'Celeste', 'vampirebat': 'Mina', 'viper': 'Vyper', 'werewolf': 'Silver',
}
DISPLAY = (
    'Abrams', 'Bebop', 'Billy', 'Calico', 'Drifter', 'Dynamo', 'Fathom', 'Grey Talon',
    'Haze', 'Holliday', 'Infernus', 'Ivy', 'Kelvin', 'Lady Geist', 'Lash', 'Magician',
    'McGinnis', 'Mina', 'Mirage', 'Mo & Krill', 'Paige', 'Paradox', 'Pocket', 'Raven',
    'Seven', 'Shiv', 'Sinclair', 'The Doorman', 'Trapper', 'Victor', 'Vindicta', 'Viper',
    'Viscous', 'Vyper', 'Warden', 'Wraith', 'Wrecker', 'Yamato',
    'Graves', 'Rem', 'Silver', 'Venator', 'Apollo', 'Celeste', 'Deadman Danny',
    'Rat King', 'Solomon', 'Violet', 'Nurse Harrow', 'Baba',
)
MULTI_HERO = '(multi-hero pack)'
OTHER = '(other)'
TYPE_LABELS = {
    'sounds': '(sound mod)', 'soundevents': '(sound mod)', 'panorama': '(UI mod)',
    'particles': '(effects mod)', 'models': '(map/props mod)', 'materials': '(map/props mod)',
}
MAX_PATHS = 200_000
BACKSLASH = chr(92)

# Where hero codenames show up in mod file paths:
#   models/heroes_wip/haze/..., materials/models/heroes_staging/yamato_v2/...  (strongest signal)
#   panorama/images/heroes/inferno_card_psd.vtex_c
#   particles/abilities/lash/..., sounds/weapons/inferno/...
HERO_FOLDER = re.compile(r'(?:^|/)heroes[^/]*/([^/]+)/', re.IGNORECASE)
HERO_CARD = re.compile(r'^panorama/images/heroes/([a-z]+)_', re.IGNORECASE)
HERO_ASSETS = re.compile(r'^(?:particles|sounds|soundevents)/', re.IGNORECASE)
VERSION_SUFFIX = re.compile(r'_v\d+$')
NON_ALNUM = re.compile(r'[^a-z0-9]')


def _squash(text: str) -> str:
    return NON_ALNUM.sub('', text.lower())


_SQUASHED = {_squash(name): name for name in DISPLAY}


def _known(code: str) -> str | None:
    name = CODENAMES.get(code.lower())
    if name:
        return name
    key = _squash(code)
    if not key:
        return None
    return _SQUASHED.get(key) or _SQUASHED.get('the' + key)


def _resolve(code: str) -> str | None:
    """Known hero for a codename, trying variants (yamato_v2, wraith_gen_man), else None."""
    current = VERSION_SUFFIX.sub('', code)
    for _ in range(current.count('_') + 1):
        name = _known(current)
        if name:
            return name
        cut = current.rfind('_')
        if cut <= 0:
            break
        current = current[:cut]
    return None


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _vote_path(votes: Counter, path: str) -> None:
    match = HERO_FOLDER.search(path)
    if match:
        code = match.group(1)
        name = _resolve(code) or _capitalize(VERSION_SUFFIX.sub('', code))
        if name:
            votes[name] += 3
        return
    match = HERO_CARD.match(path)
    if match:
        hero = _resolve(match.group(1))
        if hero:
            votes[hero] += 1
        return
    if HERO_ASSETS.match(path):
        for segment in path.split('/')[1:-1]:
            hero = _resolve(segment)
            if hero:
                votes[hero] += 1
                return


def from_files(paths) -> str:
    """Hero most referenced by the paths, MULTI_HERO for broad packs, or '' if none."""
    votes: Counter = Counter()
    for count, raw in enumerate(paths):
        if count >= MAX_PATHS:
            break
        _vote_path(votes, raw.replace(BACKSLASH, '/'))
    if not votes:
        return ''
    top_name, top_votes = votes.most_common(1)[0]
    if len(votes) >= 3 and top_votes * 2 < sum(votes.values()):
        return MULTI_HERO
    return top_name


def mod_type(paths) -> str:
    """Label for mods without hero files, from the folder most of their files live in."""
    items = list(paths)[:MAX_PATHS]
    labels = Counter(TYPE_LABELS.get(p.replace(BACKSLASH, '/').split('/')[0].lower()) for p in items)
    labels.pop(None, None)
    if not labels:
        return OTHER
    label, count = labels.most_common(1)[0]
    return label if count * 3 >= len(items) else OTHER


def describe(paths) -> str:
    """Hero name for a mod, or what kind of mod it is when no hero is found."""
    items = list(paths)[:MAX_PATHS]
    return from_files(items) or mod_type(items)


def is_hero(label) -> bool:
    """True for a real character name (labels like '(sound mod)' are not characters)."""
    return isinstance(label, str) and bool(label) and not label.startswith('(')


def canonical(name: str) -> str | None:
    """Display name for a typed hero name or codename (paige, bookworm, lady geist), else None."""
    if not isinstance(name, str) or not name.strip():
        return None
    clean = name.strip()
    return _known(clean) or _resolve(clean.lower())
