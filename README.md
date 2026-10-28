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
| `group` / `group list` / `group switch [group]` | Manage groups; only one mod per group is active |
| `group assign [mod]` / `group set <mod> <group>` / `group clear <mod>` | Put mods in groups or take them out |
| `conflicts` | Find enabled mods that replace the same files |
| `uninstall [name]` | Remove a mod's files (backups are kept) |
| `list` / `status` | Show mods by group / show the environment |
| `rollback` | Restore any backed-up file |
| `patch` / `unpatch` | Fix or remove the SearchPaths entries in `gameinfo.gi` |
| `register` / `unregister` | Make GameBanana 1-click links open dlmm (Windows) |

These options work with every command:
- `--game-dir <Deadlock folder>` sets the game location. You only need it once, because the path is remembered.
- `--downloads <folder>` changes which folder is scanned for archives.

## Groups and switching

Put mods that replace the same thing in one group, for example two skins for the same hero in a group called "Haze skins". Only one member of a group is enabled at a time. Enabling another member, or picking it in `group switch`, disables the current one.

When you install a mod, dlmm asks whether it belongs to a group. Groups whose name matches a word in the mod name are listed first.

Disabled mods are not deleted. Their files are moved to `game/citadel/dlmm_disabled/<id>/`, where the game doesn't load them. Every VPK chosen from one archive belongs to one mod and is always switched as a unit.

## Conflicts

dlmm reads each VPK's internal file list. When two enabled mods that aren't in the same group replace the same files, it shows which files overlap and offers to group the two mods.

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
