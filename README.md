<div align="center">

<img src="assets/bharat_icon.png" width="112" alt="Bharat Browser logo">

<h1>Bharat Browser</h1>

<p><b>A tiny, fast, privacy-first web browser for Linux.<br>Built on WebKitGTK. Made in India.</b></p>

[![Version](https://img.shields.io/badge/version-1.4.1-blue.svg)](https://github.com/Sangam1112/bharat-browser/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Ubuntu%20%7C%20Debian%20%7C%20Fedora%20%7C%20WSL2-orange.svg)](#-install)
[![Installer](https://img.shields.io/badge/installer-~100%20KB-brightgreen.svg)](#-small-by-design)
[![Updates](https://img.shields.io/badge/updates-signed%20(Ed25519)-6366f1.svg)](#-keeping-it-up-to-date)

[**Features**](#-features) · [**Screenshots**](#-screenshots) · [**Install**](#-install) · [**Update**](#-keeping-it-up-to-date) · [**Shortcuts**](#-keyboard-shortcuts) · [**Privacy**](#-privacy--what-it-connects-to) · [**Develop**](#-development)

<br>

<img src="docs/screenshots/offline-page.png" width="820" alt="Bharat Browser showing its friendly offline page with a playable kite game">

</div>

---

## 🌟 Why Bharat Browser?

<table>
  <tr>
    <td width="25%" valign="top"><h3>🪶 Tiny</h3>A ~100 KB installer. It uses the WebKitGTK engine already on your system instead of shipping its own 150 MB copy.</td>
    <td width="25%" valign="top"><h3>🛡️ Private</h3>Ad and tracker blocking, tracking-parameter stripping, HTTPS upgrades and third-party cookie blocking are on from the first launch.</td>
    <td width="25%" valign="top"><h3>⚡ Light</h3>Sleeps unused tabs, trims its own cache, uses your GPU and recovers crashed tabs, so it stays comfortable on older PCs.</td>
    <td width="25%" valign="top"><h3>🔐 Trustworthy</h3>Updates are cryptographically signed, passwords live in your system keyring, and there is no telemetry of any kind.</td>
  </tr>
</table>

It is a native GTK3 desktop app, not a repackaged Chromium or Electron. The ad blocker, dark mode and tracker protection are built in-house rather than bundled copies of uBlock Origin, DarkReader or ClearURLs.

---

## ✨ Features

### 🛡️ Privacy & security
- **Ad & tracker blocking** with a built-in list plus an optional **weekly EasyPrivacy update** (whole-domain, third-party rules only, with a never-block list so sign-in and captcha services keep working).
- **Tracking-parameter stripping** (`utm_*`, `fbclid`, `gclid` and friends) and **automatic HTTPS upgrades**.
- **HTTPS-only warning**: if a site can't be reached securely you get a clear page and a deliberate "continue over HTTP" choice, never a silent downgrade.
- **Third-party cookie blocking** and WebKit's Intelligent Tracking Prevention. **Fingerprint protection** nudges canvas readback, hides your real GPU/WebGL renderer and reports generic CPU/memory values. WebRTC is off by default so it can't leak your IP.
- **Per-site controls** (click the 🔒): ads, JavaScript, remembered zoom, and camera/location/notification permissions, site by site.
- **Privacy report**: click the shield to see what was stopped.
- **Password saving in your system keyring** (GNOME Keyring, KWallet or KeePassXC). Nothing is stored in the browser's own files, nothing fills without your click, and private windows never save.
- **Private windows** keep history, bookmarks, cookies, passwords and site settings out of storage entirely.

### 🌐 Browsing
- **Tabs that behave**: pin tabs (they survive restarts), right-click menu, middle-click to close, reopen the last closed tab, automatic recovery after a renderer crash.
- **Reader mode** for distraction-free articles, with text size and themes.
- **Dark mode on any site**, **find in page**, an address bar that autocompletes from your history, and a **history dashboard** ranked by time spent.
- **Bookmarks** with a manager, and **import from Firefox, Chrome, Chromium, Brave, Edge, Vivaldi, Opera** or an exported `.html`.
- **Downloads manager**, in-tab **PDF viewing**, **screenshots**, **printing / save as PDF**, a **developer inspector** (F12) and optional **spell check**.
- A friendly **offline page** that reloads itself when you're back online (with a kite game while you wait 🪁).

### ⚡ Performance
- **Background tab suspension**: tabs idle for 15+ minutes are unloaded and restored instantly, exactly as you left them. Never touches the active tab or one playing audio.
- **Low Memory Mode**, **GPU acceleration** toggle, link/DNS prefetch and smooth scrolling.
- Friendly "this tab ran out of memory" and "keeps crashing" pages instead of a blank tab.

### 🧰 Housekeeping
- **Signed, self-updating**: in-app updates are verified with an Ed25519 signature before anything is installed.
- Redesigned **Settings** with toggles, a built-in password manager and a site-settings manager.

---

## 📸 Screenshots

<table>
  <tr>
    <td align="center"><b>Reader mode</b> (Ctrl+Alt+R)<br><img src="docs/screenshots/reader-mode.png" alt="Reader mode" width="400"></td>
    <td align="center"><b>Privacy report</b><br><img src="docs/screenshots/privacy-report.png" alt="Privacy report" width="400"></td>
  </tr>
  <tr>
    <td align="center"><b>Password prompt &amp; pinned tab</b><br><img src="docs/screenshots/password-prompt.png" alt="Password prompt and a pinned tab" width="400"></td>
    <td align="center"><b>HTTPS-only warning</b><br><img src="docs/screenshots/https-warning.png" alt="HTTPS-only warning page" width="400"></td>
  </tr>
</table>

<details>
<summary><b>Settings</b> (4 tabs)</summary>
<br>
<table>
  <tr>
    <td align="center"><b>General</b><br><img src="docs/screenshots/settings-general.png" alt="General settings" width="380"></td>
    <td align="center"><b>Privacy</b><br><img src="docs/screenshots/settings-privacy.png" alt="Privacy settings" width="380"></td>
  </tr>
  <tr>
    <td align="center"><b>Performance</b><br><img src="docs/screenshots/settings-performance.png" alt="Performance settings" width="380"></td>
    <td align="center"><b>Data &amp; Actions</b><br><img src="docs/screenshots/settings-data-actions.png" alt="Data and actions settings" width="380"></td>
  </tr>
</table>
</details>

---

## 📦 Install

**Requirements:** Linux with Python 3, GTK 3, PyGObject and **WebKit2GTK 4.1** (4.0 also works). The installers below fetch these for you. Optional: a keyring service (GNOME Keyring / KWallet / KeePassXC) for saved passwords, and a hunspell dictionary for spell check.

### Quick install

Packages are on the [**Releases page**](https://github.com/Sangam1112/bharat-browser/releases/latest). Replace the version if a newer one is out.

<table>
<tr><th>Ubuntu / Debian / Mint</th><th>Fedora / RHEL</th></tr>
<tr valign="top"><td>

```bash
VERSION=1.4.1
wget https://github.com/Sangam1112/bharat-browser/releases/download/v$VERSION/bharat-browser_${VERSION}-1_all.deb
sudo apt install ./bharat-browser_${VERSION}-1_all.deb
```

</td><td>

```bash
VERSION=1.4.1
wget https://github.com/Sangam1112/bharat-browser/releases/download/v$VERSION/bharat-browser-${VERSION}-1.noarch.rpm
sudo dnf install ./bharat-browser-${VERSION}-1.noarch.rpm
```

</td></tr>
</table>

Use `apt install ./file.deb` (not `dpkg -i`) so the GTK and WebKit dependencies are resolved automatically. Then start it from your application menu, or run `bharat-browser`.

### Install from source (no root needed)

This installs for your user only (`~/.local`) and gives you the self-updater:

```bash
git clone https://github.com/Sangam1112/bharat-browser.git
cd bharat-browser
./install-ubuntu.sh        # Ubuntu / Debian / Mint
./install-fedora.sh        # Fedora / RHEL
```

Add `--system` to install for all users instead (it asks for `sudo`). If `bharat-browser` isn't found in a new terminal, add this to `~/.bashrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

<details>
<summary><b>Fedora without git</b>: use the release archive</summary>

```bash
VERSION=1.4.1
wget https://github.com/Sangam1112/bharat-browser/releases/download/v$VERSION/bharat-browser_${VERSION}_fedora.tar.gz
mkdir -p /tmp/bharat_fedora
tar -xzf bharat-browser_${VERSION}_fedora.tar.gz -C /tmp/bharat_fedora
cd /tmp/bharat_fedora && ./install-fedora.sh
```
</details>

<details>
<summary><b>Windows 10/11</b> (via WSL2 + WSLg)</summary>

There is no native Windows build. Bharat Browser runs inside **WSL2** and opens as its own window on the Windows desktop through **WSLg** (Windows 10 build 19044+ or Windows 11).

1. In PowerShell: `wsl --install -d Ubuntu`, reboot if asked, then open **Ubuntu** once to create your user.
2. Inside the Ubuntu terminal:
   ```bash
   git clone https://github.com/Sangam1112/bharat-browser.git
   cd bharat-browser
   ./install-wsl.sh
   ```
3. Run `bharat-browser`.
</details>

---

## 🔄 Keeping it up to date

- **Source or user install:** open **Settings → Data & Actions → Check for updates**. If a newer release exists it is downloaded, its signature is verified, and you just click **Restart Now**. The browser also checks quietly about 30 seconds after launch.
- **`.deb` / `.rpm` installs:** the same button works (updates go to `~/.local/share/bharat-browser`, which the launcher prefers), or install the newer package from the [Releases page](https://github.com/Sangam1112/bharat-browser/releases/latest).
- A release that is **not signed by the project key is never installed**, even if the download itself were tampered with.

## 🗑️ Uninstall

```bash
sudo apt remove bharat-browser          # Debian / Ubuntu package
sudo dnf remove bharat-browser          # Fedora package

# user install / self-updated copy
rm -rf ~/.local/share/bharat-browser ~/.local/bin/bharat-browser \
       ~/.local/share/applications/bharat-browser.desktop \
       ~/.local/share/icons/hicolor/256x256/apps/bharat-browser.png

# your data (history, bookmarks, settings, cookies) and cache
rm -rf ~/.config/bharat-browser ~/.cache/bharat-browser
```

Saved passwords are in your system keyring, not in those folders: remove them first from **Settings → Privacy → Manage saved passwords**.

---

## 🧭 Keyboard shortcuts

| Action | Shortcut | Action | Shortcut |
|---|---|---|---|
| New tab | `Ctrl+T` | Find in page | `Ctrl+F` |
| Close tab | `Ctrl+W` | Bookmark page | `Ctrl+D` |
| Reopen closed tab | `Ctrl+Shift+T` | Bookmark manager | `Ctrl+Shift+O` |
| Next / previous tab | `Ctrl+Tab` / `Ctrl+Shift+Tab` | History dashboard | `Ctrl+H` |
| New private window | `Ctrl+Shift+N` | Reader mode | `Ctrl+Alt+R` |
| Focus address bar | `Ctrl+L` | Print / save as PDF | `Ctrl+P` |
| Back / forward | `Alt+←` / `Alt+→` | Zoom in / out / reset | `Ctrl++` / `Ctrl+-` / `Ctrl+0` |
| Reload | `F5` or `Ctrl+R` | Developer inspector* | `F12` or `Ctrl+Shift+I` |

\* Turn on **Developer Tools** in Settings → Performance first. Touchpad swipe also goes back/forward.

---

## 🔒 Privacy & what it connects to

Bharat Browser has **no telemetry, analytics or accounts**. Besides the sites you visit, it makes only these connections itself:

| What | To | When | Turn off |
|---|---|---|---|
| Update check | `api.github.com`, `raw.githubusercontent.com` | ~30 s after launch, and when you click *Check for updates* | n/a (it only reads a small version file) |
| Tracker list | `easylist.to` | About once a week | Settings → Privacy → *Keep tracker lists up to date* |
| Link / DNS prefetch | the pages you hover over | While browsing | n/a |

**Where your data lives:** settings, history, bookmarks, session, per-site settings, cookies and statistics are in `~/.config/bharat-browser` (readable only by you); cache in `~/.cache/bharat-browser`; passwords only in your system keyring. Private windows write none of it to disk.

Spotted a security problem? Please open a [GitHub issue](https://github.com/Sangam1112/bharat-browser/issues), or contact the maintainer privately if it's sensitive.

---

## 🪶 Small by design

| Browser | Installer size | Installed size (approx.) |
|---|---|---|
| **Bharat Browser** | **~100 KB** (RPM) / **~85 KB** (.deb) | **~285 KB** |
| Google Chrome | ~90–100 MB | ~250–350 MB |
| Mozilla Firefox | ~55–75 MB | ~200–300 MB |
| Chromium | ~100–150 MB | ~300–400 MB |
| Brave | ~90–110 MB | ~300+ MB |
| Microsoft Edge (Linux) | ~90–100 MB | ~250–350 MB |

That's roughly **500–1000× smaller**. Chrome, Firefox, Chromium, Brave and Edge each bundle a complete rendering engine (Blink + V8, or Gecko + SpiderMonkey), typically 150–250 MB on its own. Bharat Browser ships none of that: it's a ~280 KB Python/GTK3 program that calls into **WebKitGTK**, a system library most Linux desktops already have for other GTK apps, from the same engine family as Safari.

> Bharat Browser's sizes were measured from this repository's release packages. The other browsers' figures are well-known public approximations that vary by version and platform.

---

## 🧪 Development

```bash
git clone https://github.com/Sangam1112/bharat-browser.git && cd bharat-browser
python3 bharat_browser.py            # run straight from the checkout
tools/run-tests.sh                   # all automated checks
```

The tests include unit tests for the browser's logic, real-window tests (they need a display, and use a throw-away profile and local servers, never yours) and a test that runs every install script against stubbed system tools.

| Task | Command |
|---|---|
| Bump the version everywhere | `python3 tools/bump-version.py X.Y.Z "summary"` |
| Build the `.deb` / `.rpm` / Fedora archive (also signs the release) | `./build-deb.sh` · `./build-rpm.sh` |
| Publish (push, tag, verify, GitHub Release) | `tools/release.sh` |
| Check a published release like the updater does | `python3 tools/verify-published.py` |

`package.json` is the single source of truth for the version. See [`CHANGELOG.md`](CHANGELOG.md) for what changed, and `.agents/rules/release-management.md` for the full release checklist.

---

## 🙏 Credits

Built on [WebKitGTK](https://webkitgtk.org/) and [PyGObject](https://pygobject.gnome.org/). The optional weekly tracker list is [EasyPrivacy](https://easylist.to/) by the EasyList authors.

## 📄 License

[MIT](LICENSE) © 2026 Sangam1112
