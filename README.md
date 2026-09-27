# 🇮🇳 Bharat Browser (`bharat-browser`) - v1.2.13

> **Modern, Ultra-Fast, and Privacy-First Web Browser engineered for Linux (Ubuntu)**

[![Version](https://img.shields.io/badge/version-1.2.13-blue.svg)](https://github.com/Sangam1112/bharat-browser)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Ubuntu%20%7C%20Linux-orange.svg)]()
[![Privacy](https://img.shields.io/badge/privacy-Strict%20Enforcement-red.svg)]()

---

## 🌟 Overview

**Bharat Browser** (`bharat-browser`) is a modern, high-performance web browser designed with strict security, privacy protection, and site compatibility at its core. It integrates industry-leading open-source privacy engines—including **DarkReader**, **uBlock Origin Lite / Privacy Badger**, and **ClearURLs**—into a native, hardware-accelerated Linux desktop application.

---

## ✨ Key Specifications & Features

### 🗂️ Multi-Tab Architecture & Performance Tuning
* **Native Multi-Tab Workspace**: Powered by `Gtk.Notebook` allowing instant creation (Ctrl+T), tab switching, and closing (Ctrl+W) with shared high-speed WebContext cache.
* **Smart Link Prefetching Engine**: Injected `mouseenter` hover listener pre-resolves DNS (`dns-prefetch`) and warms up TLS connections (`preconnect`) prior to user clicks.
* **Process Crash Resilience**: Automatic `web-process-terminated` signal handling auto-recovers tabs seamlessly during OOM spikes or render crashes.
* **WebKit2 DataManager Cache**: Optimized disk and RAM caching via custom `WebsiteDataManager` paths (`~/.cache/bharat-browser`).

### 🔒 Privacy & Security Defaults
* **2-Stage Request Interceptor**: High-throughput filter architecture using an $O(1)$ domain pre-lookup hash set followed by path regular expressions (maintaining throughput > 25,000 requests/sec).
* **Open-Source Ad & Tracker Blocking**: Native integration of uBlock Origin Lite and Privacy Badger filter lists to block intrusive ad servers, trackers, and telemetry scripts.
* **ClearURLs URL Sanitization**: Automatically strips privacy-invading query parameters (e.g., `utm_*`, `fbclid`, `gclid`, `msclkid`, `mc_eid`, `yclid`, `igshid`) before network requests leave the device.
* **HTTPS Enforcement**: Auto-upgrades non-secure `http://` connections to `https://` across all web navigation.
* **Security Headers Enforcement**: Injects strict HTTP response headers (`X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`) to mitigate MIME-sniffing and cross-origin leaks.

### ⚡ Performance & Hardware Acceleration
* **GPU Hardware Acceleration**: Configured with `--enable-gpu-rasterization`, `--enable-zero-copy`, and `--ignore-gpu-blocklist` for smooth 60 FPS rendering.
* **Adaptive Hardware Profiling**: Automatically detects system hardware resources:
  * **High Spec ($\ge$ 4GB RAM)**: Maximizes hardware rasterization and multi-threaded rendering pipelines.
  * **Low Spec (< 4GB RAM)**: Caps V8 JavaScript memory footprint (`--max-old-space-size=512`), throttles inactive background tab timers, and optimizes renderer process memory.

### 👁️ DarkReader Integration
* **Universal Dark Mode**: Integrates the open-source DarkReader engine to inject clean, high-contrast dark themes into every visited website without causing visual artifacting.

### 📥 Integrated Download Manager
* **Native Downloads**: Built-in download manager with collision-safe filenames, a downloads history dialog, and status-bar progress notifications (started/completed/failed).

---

## 📦 Installation Guide (Fedora & Ubuntu/Debian)

### 🔵 Fedora Linux Installation

**Option A: Install via Git / Local repository**
```bash
# 1. Clone the repository
git clone https://github.com/Sangam1112/bharat-browser.git
cd bharat-browser

# 2. Run the Fedora installer script
./install-fedora.sh
```

**Option B: Install via pre-packaged Fedora archive (`bharat-browser_1.2.13_fedora.tar.gz`)**
```bash
# 1. Extract the release archive
tar -xzf bharat-browser_1.2.13_fedora.tar.gz -C /tmp/bharat_fedora

# 2. Run installer script from archive
cd /tmp/bharat_fedora && ./install-fedora.sh
```

**Option C: Install the RPM directly (`bharat-browser-1.2.13-1.fc44.noarch.rpm`)**
```bash
sudo dnf install ./bharat-browser-1.2.13-1.fc44.noarch.rpm
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

