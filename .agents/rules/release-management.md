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
- Old package artifacts (`bharat-browser_<old-version>*`) must be cleaned up when the new version is generated.

## 2. Settings & Dialog UI Architecture
- Prefer categorized `Gtk.Stack` + `Gtk.StackSwitcher` interfaces for complex settings dialogs over long vertical scroll views.
- Always pack both `Gtk.StackSwitcher` AND `Gtk.Stack` with `pack_start(stack, True, True, 0)` into the parent dialog container.
- Group related options into `.settings-card` boxes with consistent padding and subtitle hints.
