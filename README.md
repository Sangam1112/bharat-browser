# 🇮🇳 Bharat Browser (`bharat-browser`) - v1.2.18

> **Modern, Ultra-Fast, and Privacy-First Web Browser engineered for Linux (Fedora)**

[![Version](https://img.shields.io/badge/version-1.2.18-blue.svg)](https://github.com/Sangam1112/bharat-browser)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Fedora%20%7C%20Linux-orange.svg)]()
[![Privacy](https://img.shields.io/badge/privacy-Strict%20Enforcement-red.svg)]()

---

## 🌟 Overview

**Bharat Browser** (`bharat-browser`) is a modern, high-performance web browser designed with strict security, privacy protection, and site compatibility at its core. It's a native, hardware-accelerated GTK3 + WebKit2GTK desktop application with a custom ad/tracker blocklist, tracking-parameter stripping, HTTPS upgrading, and a DarkReader-style dark mode built in-house (not bundled copies of the uBlock Origin, Privacy Badger, DarkReader, or ClearURLs projects).

---

## ✨ Key Specifications & Features

* **Fast, Native Tabs** — `Gtk.Notebook`-based multi-tab browsing (Ctrl+T / Ctrl+W) with link hover prefetch, GPU-accelerated compositing, and automatic tab recovery after renderer crashes.
* **Built-In Privacy** — Custom ad/tracker domain blocking, tracking-parameter stripping (`utm_*`, `fbclid`, `gclid`, etc.), automatic HTTPS upgrades, and GPU/renderer fingerprint spoofing — all on by default, all toggleable.
* **Dark Mode Everywhere** — One-click, high-contrast dark styling injected into any site.
* **Smart Resource Handling** — Custom disk cache, low-memory cache trimming, and crash-resilient tabs keep the browser responsive under pressure.
* **Built-In Downloads** — Native download manager with collision-safe filenames, history, and progress notifications.

---

## 📦 Installation Guide (Fedora, plus Windows via WSL2)

### 🔵 Fedora Linux Installation

**Option A: Install via Git / Local repository**
```bash
# 1. Clone the repository
git clone https://github.com/Sangam1112/bharat-browser.git
cd bharat-browser

# 2. Run the Fedora installer script
./install-fedora.sh
```

**Option B: Install via pre-packaged Fedora archive (`bharat-browser_1.2.18_fedora.tar.gz`)**
```bash
# 1. Extract the release archive
tar -xzf bharat-browser_1.2.18_fedora.tar.gz -C /tmp/bharat_fedora

# 2. Run installer script from archive
cd /tmp/bharat_fedora && ./install-fedora.sh
```

**Option C: Install the RPM directly (`bharat-browser-1.2.18-1.fc44.noarch.rpm`)**
```bash
sudo dnf install ./bharat-browser-1.2.18-1.fc44.noarch.rpm
```

### 🪟 How to run on Windows 10/11

Bharat Browser is a GTK3 + WebKit2GTK app with no native Windows build, so
on Windows it runs inside **WSL2**, using **WSLg** to display the app as its
own window on the Windows desktop (no separate VM window, no X server setup).

> WSLg (and this flow) requires Windows 10 build 19044+ or Windows 11.

**Step 1 — One-time WSL2 setup (run in PowerShell, not inside Linux):**
```powershell
wsl --install -d Ubuntu
```
Reboot if prompted, then open **Ubuntu** from the Start menu once to finish
first-run setup (pick a username/password).

**Step 2 — Install Bharat Browser (run inside the Ubuntu/WSL terminal):**
```bash
git clone https://github.com/Sangam1112/bharat-browser.git
cd bharat-browser
./install-wsl.sh
```
This installs the required GTK3/WebKit2GTK dependencies via `apt` and sets
up a `bharat-browser` command.

**Step 3 — Launch it:**
```bash
bharat-browser
```
The browser opens as its own window directly on your Windows 10/11 desktop
via WSLg. If the command isn't found in new terminals, add
`export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc`.

---

## 🚀 Launching Bharat Browser

Run from terminal:
```bash
bharat-browser
```
Or launch **Bharat Browser** directly from your desktop application launcher menu.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

