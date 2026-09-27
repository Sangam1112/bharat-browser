#!/bin/bash
# Installation script for Bharat Browser on native Ubuntu/Debian Linux
# (System or User mode). For running inside WSL2 on Windows, use
# install-wsl.sh instead — the dependency install is the same, but that
# script skips desktop-shortcut/icon registration since WSLg launches it
# straight from the command it installs.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Installing Bharat Browser on Ubuntu/Debian Linux"
echo "=========================================="
echo "(Installs to ~/.local by default. Pass --system for a system-wide"
echo " /usr install; you'll be prompted for sudo.)"

USE_SUDO=false
if [ "$EUID" -ne 0 ]; then
    if [ "$1" = "--system" ]; then
        if sudo -v; then
            USE_SUDO=true
        else
            echo "System-wide install requested but sudo authentication failed." >&2
            exit 1
        fi
    fi
    # No silent escalation: passwordless sudo being configured for unrelated
    # reasons shouldn't change this script's behavior from a user-local
    # install to a system-wide one without the user asking for it.
else
    USE_SUDO=true
fi

echo "[1/4] Installing dependencies via apt..."
sudo apt-get update
sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 git

# Ubuntu 24.04+ ships webkit2gtk 4.1; older releases only have 4.0.
if apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
    sudo apt-get install -y gir1.2-webkit2-4.1
else
    sudo apt-get install -y gir1.2-webkit2-4.0
fi

if [ "$USE_SUDO" = true ]; then
    INSTALL_DIR="/usr/share/bharat-browser"
    BIN_DIR="/usr/bin"
    DESKTOP_DIR="/usr/share/applications"
    ICON_DIR="/usr/share/icons/hicolor/256x256/apps"
    CMD_PREFIX="sudo"
else
    echo "Installing in user local directory (~/.local)..."
    INSTALL_DIR="${HOME}/.local/share/bharat-browser"
    BIN_DIR="${HOME}/.local/bin"
    DESKTOP_DIR="${HOME}/.local/share/applications"
    ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
    CMD_PREFIX=""
fi

echo "[2/4] Copying application files..."
$CMD_PREFIX mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/assets" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

$CMD_PREFIX cp bharat_browser.py "$INSTALL_DIR/"
if [ -d "assets" ]; then
    $CMD_PREFIX cp -r assets/* "$INSTALL_DIR/assets/"
fi

# Create launcher script wrapper
cat << 'EOF' > /tmp/bharat-browser-launcher
#!/bin/bash
SCRIPT_PATH="$(readlink -f "$0")"
BIN_DIR="$(dirname "$SCRIPT_PATH")"

# User-writable install checked first: it's the only copy the browser's
# self-updater can actually rewrite in place. A root-owned /usr/share install
# is left as a fallback for systems that only have a system-wide install.
if [ -f "${HOME}/.local/share/bharat-browser/bharat_browser.py" ]; then
    exec python3 "${HOME}/.local/share/bharat-browser/bharat_browser.py" "$@"
elif [ -f "/usr/share/bharat-browser/bharat_browser.py" ]; then
    exec python3 /usr/share/bharat-browser/bharat_browser.py "$@"
else
    exec python3 bharat_browser.py "$@"
fi
EOF
chmod +x /tmp/bharat-browser-launcher
$CMD_PREFIX cp /tmp/bharat-browser-launcher "$BIN_DIR/bharat-browser"
$CMD_PREFIX chmod +x "$BIN_DIR/bharat-browser" "$INSTALL_DIR/bharat_browser.py"

echo "[3/4] Registering desktop shortcut..."
sed "s|Exec=bharat-browser|Exec=${BIN_DIR}/bharat-browser|g" bharat-browser.desktop > /tmp/bharat-browser.desktop
$CMD_PREFIX cp /tmp/bharat-browser.desktop "$DESKTOP_DIR/bharat-browser.desktop"

if [ -f "assets/bharat_icon.png" ]; then
    $CMD_PREFIX cp assets/bharat_icon.png "$ICON_DIR/bharat-browser.png"
fi

echo "[4/4] Updating desktop environment databases..."
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

echo "=========================================="
echo "Bharat Browser successfully installed!"
echo "Binary location: ${BIN_DIR}/bharat-browser"
echo "Launch by typing '${BIN_DIR}/bharat-browser' in terminal or via Application Menu."
if [ "$USE_SUDO" = false ]; then
    echo "(add \"export PATH=\\\"\$HOME/.local/bin:\$PATH\\\"\" to ~/.bashrc if the"
    echo " \"bharat-browser\" command isn't found in new terminals)"
fi
echo "=========================================="
