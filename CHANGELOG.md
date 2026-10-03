# Changelog

All notable changes to Bharat Browser. Versions follow `MAJOR.MINOR.PATCH`.
Releases are signed; the in-app updater only installs a release whose signature verifies.

## [1.4.4] - 2026-10-04

### Added
- **Tab Memory** (main menu): see how much memory each tab uses, heaviest first, and go to, suspend or close a tab right from the list. Right-clicking a tab also shows its memory. WebKit does not say which renderer process belongs to which tab, so each tab is matched by a short CPU probe (about 0.2 s per tab, cached afterwards); a tab that can't be matched, for example one with JavaScript turned off, shows "—". Memory is the process's proportional set size, so shared libraries are not counted once per tab.

## [1.4.3] - 2026-10-03

### Fixed
- **Path-based ad/tracker blocking now actually blocks.** Requests matching `/ads/`, `/adserver/`, `/pagead/`, `/pixel.gif`, `/tracker.js`, `/telemetry`, `/analytics.js`, `/gtm.js`, `/collect?` and `/log_event` were only counted by the old Python check; WebKit still sent them. They are now rules in the native content blocker. They apply to images, scripts, styles, fonts, media and fetch/XHR only, never to a page you navigate to, and streaming hosts (YouTube, Vimeo, Twitch) and `.m3u8`/`.mpd` manifests are exempt. The per-site "Block ads" switch turns them off too.
- The diagnose tool reported "Main process memory is growing" when a second browser instance started during a run, because it summed all main processes into one series. It now fits each process on its own.

### Changed
- The per-request Python ad matcher (and the optional `adblockparser` engine) is removed: the native blocker handles all blocking inside the web process.
- The shield button no longer shows a count, and the Privacy Report no longer shows "Trackers & ads blocked": WebKit does not tell the browser about requests its content blocker drops, so those numbers were misleading. Tracking-parameter and HTTPS-upgrade counts are unchanged.

## [1.4.2] - 2026-10-03

### Changed
- **Red Hat family:** the RPM now requires `(webkit2gtk4.1 or webkit2gtk3)` and `install-fedora.sh` accepts either, trying Fedora's `webkit2gtk4.1` first and falling back to `webkit2gtk3` (RHEL, Rocky Linux, AlmaLinux, CentOS Stream). The app itself already ran on WebKit2GTK 4.1 or 4.0. Not yet tested on a real RHEL-family system; the installer paths are covered by automated tests with stubbed `rpm`/`dnf`.
- Clearer manual-install instructions when dependencies can't be installed automatically.

## [1.4.1] - 2026-10-03

### Fixed
- "Check for updates" could report the old version for a few minutes after a release, because GitHub caches the raw file address. It now asks GitHub's API first and falls back to the raw address. Updates are still only installed if the release signature verifies.
- When an update can't be installed automatically, the message now says where to download it instead of mentioning `git pull`.

## [1.4.0] - 2026-10-03

### Added
- **Tabs:** pin tabs (they survive restarts), right-click tab menu (reload, duplicate, pin, close others/to the right), middle-click to close, Ctrl+Shift+T to reopen the last closed tab.
- **Reader mode** (Ctrl+Alt+R): clean, distraction-free article view with text size and theme controls.
- **Per-site controls** from the lock icon: block ads, allow JavaScript, remembered zoom, and remembered camera/microphone, location and notification choices, with a manager in Settings.
- **Password saving** through the system keyring (GNOME Keyring, KWallet, KeePassXC) with a "Save?" / "Fill?" bar and a password manager. Never stored in the browser's own files, never filled without a click, never in private windows.
- **Import** bookmarks and history from Firefox, Chrome, Chromium, Brave, Edge, Vivaldi and Opera, or from an exported bookmarks `.html`.
- **Tracker list updates:** weekly EasyPrivacy download (whole-domain, third-party rules only, with a never-block list for sign-in and captcha services). Compiled once and cached, so launches stay instant.
- **Privacy report** (click the shield): trackers blocked, tracking parameters removed and connections upgraded to HTTPS.
- **HTTPS-only warning:** when a site can't be reached securely you get a clear page and a deliberate "continue over HTTP" choice (single-use token, so a website can't switch HTTPS upgrading off).
- Spell check toggle; a menu on the ☰ button; friendly "this tab ran out of memory" and "keeps crashing" pages.
- Tooling: `tools/bump-version.py`, `tools/check-version.py`, `tools/release.sh`, `tools/verify-published.py`, `tools/run-tests.sh`, and an automated test suite (`tests/`).

### Fixed
- Tab titles were squeezed to "…"; tabs now have a proper minimum width.
- The 🇮🇳 emoji showed as "IN" on systems without a flag font.

## [1.3.7] - 2026-10-03

- Friendly offline page (clear message, auto-reload when back online, kite mini-game); update check no longer claims "latest version" when offline.
- Redesigned Settings: banner, icon badges, toggle switches, version/signing chips.
- README screenshots.

## [1.3.6] - 2026-10-03

- Auto-updater now requires a valid Ed25519 signature on every release, verified against a public key built into the app.

## [1.3.5] - 2026-10-03

- User agent updated from Chrome 126 to Chrome 154 and moved to a single constant (`CHROME_UA_MAJOR`).

## [1.3.4] - 2026-10-03

- Persistent SQLite cookies (logins survive restarts), third-party cookie blocking and ITP.
- Fullscreen video hides the browser chrome; touchpad swipe back/forward.
- F12 / Ctrl+Shift+I inspector, Ctrl+P print, Ctrl+L, F5, Alt+Left/Right, Ctrl+Tab.

## [1.3.3] - 2026-10-03

- Self-updater works for system-wide package installs by installing to `~/.local/share/bharat-browser`.
- Fixed a stale checksum in `package.json` that made updates fail the integrity check.

## [1.3.2] - 2026-10-03

- Performance and memory fixes: signal disconnection on tab close, throttled shield badge updates, O(1) ad-block pre-lookup, stale cache cleanup.

## [1.3.1] - 2026-09-30

- GPU detection moved off the startup path; history/session writes debounced and compacted; autocomplete model updated in place.

## [1.3.0] - 2026-09-30

- Downloads Manager: choose the download folder.
- Bookmarks: address-bar star, Ctrl+D, Bookmark Manager (Ctrl+Shift+O).
- Dark mode is now a WebKit user stylesheet applied by the engine: no white flash, survives navigation and restarts.

## Earlier

Versions 1.2.x and before: see the git history and the `%changelog` in `build-rpm.sh`.
