# 🇮🇳 Bharat Browser (`bharat-browser`) - v1.0.8

> **Modern, Ultra-Fast, and Privacy-First Web Browser engineered for Linux (Ubuntu)**

[![Version](https://img.shields.io/badge/version-1.0.8-blue.svg)](https://github.com/Sangam1112/bharat-browser)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Ubuntu%20%7C%20Linux-orange.svg)]()
[![Privacy](https://img.shields.io/badge/privacy-Strict%20Enforcement-red.svg)]()

---

## 🌟 Overview

**Bharat Browser** (`bharat-browser`) is a modern, high-performance web browser designed with strict security, privacy protection, and site compatibility at its core. It integrates industry-leading open-source privacy engines—including **DarkReader**, **uBlock Origin Lite / Privacy Badger**, and **ClearURLs**—into a native, hardware-accelerated Linux desktop application.

---

## ✨ Key Specifications & Features

### 🔒 Privacy & Security Defaults
* **2-Stage Request Interceptor**: High-throughput filter architecture using an $O(1)$ domain pre-lookup hash set followed by path regular expressions (maintaining throughput > 25,000 requests/sec).
* **Open-Source Ad & Tracker Blocking**: Native integration of uBlock Origin Lite and Privacy Badger filter lists to block intrusive ad servers, trackers, and telemetry scripts.
* **ClearURLs URL Sanitization**: Automatically strips privacy-invading query parameters (e.g., `utm_*`, `fbclid`, `gclid`, `msclkid`) before network requests leave the device.
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
* **Native Downloads**: Built-in download manager featuring real-time download speed calculation, progress tracking, file organization, and desktop notifications.

### 🌐 Universal Website Compatibility
* **Native WebContents Layer**: Guarantees compatibility with modern Web Standards (HTML5, WebAssembly, WebGL, Progressive Web Apps) and complex web platforms (Google Workspace, YouTube, GitHub, trading portals).

### 📸 Page Capture & Productivity
* **Instant Webpage Screenshot**: Built-in option to capture full webpage screenshots instantly saved as JPEG images directly to your Desktop.
* **Privacy Shield Stats**: Real-time dashboard widget displaying the cumulative total of blocked trackers and ad requests.
* **Git Auto-Update Sync**: Automatically queries GitHub API on startup and via manual "Check for Updates" button; downloads higher `.deb` releases in the background, alerts the user upon upgrade completion, and updates the About section dynamically.

---

## 📦 Package Details & Ubuntu Installation

Bharat Browser produces a fully versioned `.deb` package built specifically for Ubuntu and Debian-based Linux distributions.

### Installing via `.deb` Package

```bash
# Download or locate the generated .deb file
sudo dpkg -i bharat-browser_1.0.8_amd64.deb

# Resolve any missing dependencies if prompted
sudo apt-get install -f
```

### Launching Bharat Browser

Run from terminal:
```bash
bharat-browser
```
Or launch **Bharat Browser** directly from your desktop application launcher menu.

---

## 🛠️ Building `.deb` Package from Source

To compile and package the browser as a `.deb` binary:

```bash
# Clone the public repository
git clone https://github.com/Sangam1112/bharat-browser.git
cd bharat-browser

# Install dependencies
npm install

# Build versioned .deb package
npm run build:deb
```

The resulting package will be generated at `./bharat-browser_1.0.8_amd64.deb`.

---

## 📂 Project Architecture

```
bharat-browser/
├── main.js                  # Main process: session hooks, security headers, hardware profiling
├── preload.js               # IPC bridge & context isolation layer
├── build-deb.js             # Automated Ubuntu .deb packager
├── package.json             # App metadata & dependency configuration
├── README.md                # Project documentation & technical specifications
├── src/
│   ├── adblocker.js         # 2-Stage filter (O(1) domain lookup + path regex)
│   ├── clearurls.js         # URL query parameter sanitizer
│   ├── darkreader.js        # DarkReader CSS generator
│   └── downloadManager.js   # Multi-threaded download state manager
└── renderer/
    ├── index.html           # Dark glassmorphism browser UI frame
    ├── app.js               # Tab manager, navigation control & renderer logic
    └── style.css            # Custom styling system
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
