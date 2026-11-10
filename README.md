# Cursor usage

A [Herdr](https://herdr.dev) mod that shows the Cursor quota already signed in on this Mac. A **Usage** button in the tab strip opens one dashboard: the weekly included percent, this month's balance, tokens per day, and AI line edits. Plans without weekly included usage get the included percentage, request quota, or on-demand spend instead.

The numbers come from the same local session and `cursor.com` dashboard calls as [cursor-usage](https://github.com/edison-cc/cursor-usage). Codex and Claude are not part of this mod.

## Requirements

- The Herdr app, running
- macOS, with Cursor installed and signed in
- `python3` (Xcode Command Line Tools) and `curl`

The first fetch can ask for Keychain access to **Cursor Safe Storage**. The token is sent only to `cursor.com`.

## Install

From this directory:

```sh
python3 scripts/install.py
```

The installer checks the mod in a temporary Herdr home, copies `mods/cursor-usage` into `~/.config/herdr-app/mods/cursor-usage`, and clones the layout that is on screen. The button is added only on that clone. The layout you were using is not edited.

```sh
python3 scripts/uninstall.py
```

Uninstall switches back to the previous layout and deletes the clone, but only if that clone was not edited. It does not change `keys.json`, Insights, or Herdr's shipped files. If `mods/cursor-usage` is already there and this installer did not create it, install and uninstall both stop.

`--app-home` selects another Herdr home. `--force` on install replaces owned files you edited.

## What it reads

`cursor_usage.py` reads Cursor's local database and, when the token is encrypted, the **Cursor Safe Storage** Keychain item. It then calls the Cursor dashboard. It does not print the token.
