#!/bin/bash
# Installation script for Bharat Browser inside WSL2 (Ubuntu/Debian) on Windows 11.
#
# Windows 11 ships WSLg, which runs Linux GUI apps side-by-side with Windows
# apps (its own taskbar entry, no separate VM window). Bharat Browser is a
# GTK3 + WebKit2GTK app with no native Windows build of its rendering engine,
# so this runs the real, unmodified app inside WSL2 instead of a native port.
#
# One-time setup on the Windows 11 side (run in PowerShell, not in here):
#   wsl --install -d Ubuntu
# then reboot if prompted, open "Ubuntu" from the Start menu once to finish
# first-run setup (pick a username/password), and run this script inside it.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Installing Bharat Browser (WSL2 / Windows 11)"
echo "=========================================="

if ! grep -qi microsoft /proc/version 2>/dev/null; then
    echo "Note: this doesn't look like a WSL environment, continuing anyway."
fi

echo "[1/3] Installing dependencies via apt..."
sudo apt-get update
sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 git

# Ubuntu 24.04+ ships webkit2gtk 4.1; older releases only have 4.0.
if apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
    sudo apt-get install -y gir1.2-webkit2-4.1
else
    sudo apt-get install -y gir1.2-webkit2-4.0
fi

INSTALL_DIR="${HOME}/.local/share/bharat-browser"
BIN_DIR="${HOME}/.local/bin"

echo "[2/3] Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR/assets" "$BIN_DIR"
cp bharat_browser.py "$INSTALL_DIR/"
if [ -d "assets" ]; then
    cp -r assets/* "$INSTALL_DIR/assets/"
fi
chmod +x "$INSTALL_DIR/bharat_browser.py"

cat << 'EOF' > "$BIN_DIR/bharat-browser"
#!/bin/bash
if [ -f "${HOME}/.local/share/bharat-browser/bharat_browser.py" ]; then
    exec python3 "${HOME}/.local/share/bharat-browser/bharat_browser.py" "$@"
else
    exec python3 bharat_browser.py "$@"
fi
EOF
chmod +x "$BIN_DIR/bharat-browser"

echo "[3/3] Done."
echo "=========================================="
echo "Bharat Browser installed for WSLg."
echo "Run it with:  ${BIN_DIR}/bharat-browser"
echo "(add \"export PATH=\\\"\$HOME/.local/bin:\$PATH\\\"\" to ~/.bashrc if the"
echo " \"bharat-browser\" command isn't found in new terminals)"
echo "It will open as its own window on the Windows 11 desktop via WSLg."
echo "=========================================="
