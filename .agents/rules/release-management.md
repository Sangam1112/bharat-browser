---
trigger: always_on
description: Bharat Browser release management and version synchronization standards for RPM and DEB packages.
---

# Bharat Browser Release & Packaging Standards

## 1. Releasing
- Change the version with `python3 tools/bump-version.py X.Y.Z "one-line summary"`. It updates `package.json` (the single source of truth), `bharat_browser.py`, the README, `CHANGELOG.md` and the RPM changelog. `build-deb.sh` and `build-rpm.sh` read the version from `package.json` and refuse to build if anything disagrees (`tools/check-version.py`). Expand the notes in `CHANGELOG.md` by hand.
- Run `tools/run-tests.sh` before every release.
- Run both `./build-deb.sh` and `./build-rpm.sh`: they refresh the `sha256` in `package.json` and sign the release (Ed25519, key at `~/.config/bharat-browser-signing/release-ed25519.pem`, which must never be committed). If a build prints "release NOT signed", do not publish it. Changing `UPDATE_PUBLIC_KEY_HEX` locks every installed version out of auto-updating.
- Clean up old package artifacts (`bharat-browser*<old-version>*`) in the repo root and `~/Downloads`.
- Commit, then run `tools/release.sh`: it pushes `master`, creates and pushes the `vX.Y.Z` tag, verifies the published release exactly as the in-app updater will (`tools/verify-published.py`) and creates the GitHub Release with the packages attached. The tag is essential: the updater downloads `bharat_browser.py` from it, and without it every installed browser fails to self-update.

## 2. Settings & Dialog UI Architecture
- Prefer categorized `Gtk.Stack` + `Gtk.StackSwitcher` interfaces for complex settings dialogs over long vertical scroll views.
- Always pack both `Gtk.StackSwitcher` AND `Gtk.Stack` with `pack_start(stack, True, True, 0)` into the parent dialog container.
- Group related options into `.settings-card` boxes with consistent padding and subtitle hints.
