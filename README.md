# Deadlock Lite Mod Manager (dlmm)

A small, readable Python CLI that installs Deadlock mods, uninstalls them, and backs up every game file it changes.

## Setup

```
pip install -r requirements.txt
```

`.rar` archives need an external extractor. dlmm looks for each of these in turn: WinRAR's `UnRAR.exe`, 7-Zip's `7z.exe`, `7zz`, and finally `bsdtar` or the `tar.exe` built into Windows. Installing [7-Zip](https://7-zip.org) is the easiest option. Run `python deadlock_mm.py status` to check what was found.

## Usage

| Command | What it does |
|---|---|
| `python deadlock_mm.py` | Interactive menu |
| `install [archive] [--name N]` | Install a mod; without a path, opens a picker over Downloads, newest first |
| `uninstall [name]` | Remove a mod's files (backups are kept) |
| `list` / `status` | Show installed mods / show the environment |
| `rollback` | Restore any backed-up file |
| `patch` / `unpatch` | Add or remove `Game citadel/addons` in `gameinfo.gi` |
| `register` / `unregister` | Make GameBanana 1-click links open dlmm (Windows) |

These options work with every command:
- `--game-dir <Deadlock folder>` sets the game location. You only need it once, because the path is remembered.
- `--downloads <folder>` changes which folder is scanned for archives.

## What it changes on disk

- **`Deadlock/game/citadel/addons/pakNN_dir.vpk`**: only the `.vpk` files from an archive are copied here. Each one goes into the first free slot from 01 to 99, and the slot number sets the load order.
- **`Deadlock/game/citadel/gameinfo.gi`**: `Game citadel/addons` becomes the first SearchPaths entry. Game updates can undo this; if so, run `patch` again (the menu also offers it).
- **`%APPDATA%/dlmm/`** holds dlmm's own data:
  - `installed.json` is the manifest of installed mods.
  - `config.json` stores settings, including the 1-click handler that was registered before dlmm.
  - `backups/` holds the backups.

## Backups

Before dlmm overwrites or deletes a file, it copies it to `backups/<id>/` along with its original path, the reason and a SHA-256 hash. Use `rollback` to restore any of them. A rollback backs up the current file first, so rollbacks can be undone too.

The first backup of `gameinfo.gi`, which is the vanilla file, is pinned and never pruned. Other backups are pruned down to the newest 100 or 8 GB, whichever limit is hit first.

## Safety

- Archive members with absolute paths, `..`, drive letters or symlinks are rejected.
- Nothing from an archive is executed.
- 1-click links must match `gamebanana.com/mmdl/<id>,Mod,<id>` exactly, and the file has to appear in that mod's public file list before anything is downloaded.
- Downloads are HTTPS only and size-capped.
- Uninstall only deletes files whose hash still matches what dlmm installed.
