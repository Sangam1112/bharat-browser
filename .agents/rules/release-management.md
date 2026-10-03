---
trigger: always_on
description: Bharat Browser release management and version synchronization standards for RPM and DEB packages.
---

# Bharat Browser Release & Packaging Standards

## 1. Version Invariants
- Whenever updating browser features or preparing a release, the version number must be updated consistently across:
  - `bharat_browser.py` (header docstring & `self.current_version`)
  - `package.json` (`version` field & `sha256` hash of `bharat_browser.py`)
  - `build-deb.sh` (`VERSION` variable)
  - `build-rpm.sh` (`VERSION` variable & `%changelog` block)
  - `README.md` (badges, install command examples, package names)
- Both `./build-deb.sh` and `./build-rpm.sh` must be executed to produce matching binary packages in the repo root.
- `package.json` also carries an Ed25519 `signature` that the in-app updater requires. `tools/update-checksum.sh` (called by both build scripts) signs with the private key at `~/.config/bharat-browser-signing/release-ed25519.pem`, which must never be committed. If the build prints "release NOT signed", do not publish it. Check with `python3 tools/sign-release.py verify`.
- Changing `UPDATE_PUBLIC_KEY_HEX` locks out every installed version from auto-updating; avoid it unless the key is compromised.
- After pushing the release commit, create and push a git tag `vX.Y.Z` on it (`git tag vX.Y.Z && git push origin vX.Y.Z`). The in-app updater downloads `bharat_browser.py` from that tag, so a release without its tag makes every installed browser fail to self-update (it falls back to "update via package manager"). Wait a minute, then confirm `https://raw.githubusercontent.com/Sangam1112/bharat-browser/vX.Y.Z/bharat_browser.py` returns 200.
- Old package artifacts (`bharat-browser_<old-version>*`) must be cleaned up when the new version is generated.

## 2. Settings & Dialog UI Architecture
- Prefer categorized `Gtk.Stack` + `Gtk.StackSwitcher` interfaces for complex settings dialogs over long vertical scroll views.
- Always pack both `Gtk.StackSwitcher` AND `Gtk.Stack` with `pack_start(stack, True, True, 0)` into the parent dialog container.
- Group related options into `.settings-card` boxes with consistent padding and subtitle hints.
