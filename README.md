# Deadlock Lite Mod Manager (dlmm)

A small, readable Python CLI that installs Deadlock mods, switches between them, uninstalls them, and backs up every game file it changes.

## Setup

```
pip install -r requirements.txt
```

`.rar` archives need an external extractor. dlmm looks for each of these in turn: WinRAR's `UnRAR.exe`, 7-Zip's `7z.exe`, `7zz`, and finally `bsdtar` or the `tar.exe` built into Windows. Installing [7-Zip](https://7-zip.org) is the easiest option. Run `python deadlock_mm.py status` to check what was found.

## Usage

| Command | What it does |
|---|---|
| `python deadlock_mm.py` | Interactive menu |
| `install [archive] [--name N] [--group G \| --no-group]` | Install a mod; without a path, opens a picker over Downloads, newest first |
| `enable [name]` / `disable [name]` / `toggle` | Switch mods on or off without deleting them |
| `hero` / `hero list` / `hero switch [hero]` | Manage characters; only one mod per character is active |
| `hero set <mod> [hero]` / `hero share <mod>` | Set character manually or allow sharing |
| `violet [name] [--all] [--undo]` | Check and heal stale camera attachments using vpkmerge |
| `pack [name] [--undo]` | Pack multi-VPK mods into a single consolidated VPK file |
| `conflicts [--reset-ignored]` | Find enabled mods that replace the same files, and mods installed twice |
| `merge [target]` / `install --into <mod>` | Combine mods into one, e.g. a skin and its separately downloaded voicelines |
| `uninstall [name]` | Remove a mod's files (backups are kept) |
| `list` / `status` | Show mods by group / show the environment |
| `rollback` | Restore any backed-up file |
| `patch` / `unpatch` | Fix or remove the SearchPaths entries in `gameinfo.gi` |
| `register` / `unregister` | Make GameBanana 1-click links open dlmm (Windows) |

These options work with every command:
- `--game-dir <Deadlock folder>` sets the game location. You only need it once, because the path is remembered.
- `--downloads <folder>` changes which folder is scanned for archives.

## Character Exclusivity

dlmm automatically identifies which character a skin is for based on paths in its VPK (e.g. `heroes_wip/bookworm` -> `Paige`, `engineer` -> `McGinnis`).

**Rule: Two mods cannot occupy the same character.** Enabling one automatically parks any active skin for that hero. You can override this rule per-mod using `hero share <mod>` or `--share` on install.

## Violet Fix & Packing

Using official, SHA-256 verified `vpkmerge v0.22.1` by Slush97:
- `violet`: Fixes broken camera attachments caused by the Violet update. Verifies files before saving, and creates pinned backups you can revert with `violet --undo`.
- `pack`: Combines multi-part downloads into one unified `.vpk` so they take only one slot. Later inputs win collisions. Revert anytime with `pack --undo`.

Every new install (from Downloads, a 1-click link or `install --into`) is checked automatically. If it has stale attachments, dlmm asks whether to fix it now. Skip the check once with `install --no-violet-check`, or switch it off with `violet --autocheck off`.

Disabled mods are not deleted. Their files are moved to `game/citadel/dlmm_disabled/<id>/`, where the game doesn't load them. Every VPK chosen from one archive belongs to one mod and is always switched as a unit.

## Sorting

Every list of mods can be re-sorted while it is open. Press `s` to cycle between character, newest download first and name, and press `r` to reverse the order. Your choice is remembered. The Downloads picker sorts by newest download or name. Use `list --sort character|time|name` to sort the printed list.

## Conflicts

dlmm reads each VPK's internal file list. When two enabled mods that aren't in the same group replace the same game files, it shows which files overlap. You can then group the two mods, skip the pair for now, ignore it permanently, or stop checking.

Some files don't count as conflicts: readme, license, credits and changelog files, and root-level files that aren't compiled resources (`*_c`).

## Combining mods

Use `merge` to fold separately downloaded parts, such as voicelines, into one mod. They are then enabled, disabled, grouped and uninstalled together. You can also add an archive directly to an existing mod with `install --into <mod>`.

## What it changes on disk

- **`Deadlock/game/citadel/addons/pakNN_dir.vpk`**: the enabled mods' VPKs. Each one goes into a free slot from 01 to 99, and lower numbers win when mods overlap.
- **`Deadlock/game/citadel/dlmm_disabled/`**: the VPKs of disabled mods.
- **`Deadlock/game/citadel/gameinfo.gi`**: `Game citadel/addons` becomes the first SearchPaths entry, and any missing `Mod`/`Write` paths are added. Game updates can reset this; if so, run `patch` again (the menu also offers it).
- **`%APPDATA%/dlmm/`** holds dlmm's own data:
  - `installed.json` is the manifest.
  - `config.json` stores settings.
  - `backups/` holds the backups.

## Backups

Before dlmm overwrites or deletes a file, it copies it to `backups/<id>/` along with its original path, the reason and a SHA-256 hash. Use `rollback` to restore any of them. A rollback backs up the current file first, so rollbacks can be undone too.

The first backup of `gameinfo.gi` is pinned and never pruned. Other backups are pruned down to the newest 100 or 8 GB, whichever limit is hit first.

## Safety

- Archive members with absolute paths, `..`, drive letters or symlinks are rejected.
- Nothing from an archive is executed.
- 1-click links are strictly validated, and the file has to belong to the stated mod before anything is downloaded.
- Before a file is moved or deleted, its hash is checked against what dlmm installed. A failed multi-file move is rolled back.
