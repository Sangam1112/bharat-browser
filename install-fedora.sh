#!/bin/bash
# Installation script for Bharat Browser on Fedora Linux (System or User mode)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Installing Bharat Browser on Fedora Linux"
echo "=========================================="

USE_SUDO=false
if [ "$EUID" -ne 0 ]; then
    if sudo -n true 2>/dev/null; then
        USE_SUDO=true
    fi
else
    USE_SUDO=true
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

echo "[1/3] Copying application files..."
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
# is left as a fallback for systems that only have the RPM installed.
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

echo "[2/3] Registering desktop shortcut..."
sed "s|Exec=bharat-browser|Exec=${BIN_DIR}/bharat-browser|g" bharat-browser.desktop > /tmp/bharat-browser.desktop
$CMD_PREFIX cp /tmp/bharat-browser.desktop "$DESKTOP_DIR/bharat-browser.desktop"

if [ -f "assets/bharat_icon.png" ]; then
    $CMD_PREFIX cp assets/bharat_icon.png "$ICON_DIR/bharat-browser.png"
fi

echo "[3/3] Updating desktop environment databases..."
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

echo "=========================================="
echo "Bharat Browser successfully installed!"
echo "Binary location: ${BIN_DIR}/bharat-browser"
echo "Launch by typing '${BIN_DIR}/bharat-browser' in terminal or via Application Menu."
echo "=========================================="
