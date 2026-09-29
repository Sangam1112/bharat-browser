#!/bin/bash
# Installation script for Bharat Browser on Fedora Linux (System or User mode)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Installing Bharat Browser on Fedora Linux"
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

# dnf always needs root regardless of whether the app itself is being
# installed system-wide or to ~/.local; but if we're already root (e.g. a
# minimal container image), "sudo" may not even be installed, so don't
# invoke it unnecessarily in that case.
DNF_PREFIX="sudo"
if [ "$EUID" -eq 0 ]; then
    DNF_PREFIX=""
fi

# Same runtime dependencies build-rpm.sh declares in the RPM's Requires line.
REQUIRED_PKGS="python3 python3-gobject gtk3 webkit2gtk4.1"

# Tell the user exactly what is missing and how to install it by hand, then
# stop: continuing would leave a browser that dies on launch with an
# unhelpful Python ImportError.
print_manual_instructions() {
    echo "" >&2
    echo "ERROR: $1" >&2
    echo "" >&2
    echo "Bharat Browser needs these packages, which are not installed:" >&2
    for pkg in $MISSING_PKGS; do
        echo "  - $pkg" >&2
    done
    echo "" >&2
    echo "Install them manually, then re-run this script:" >&2
    echo "  sudo dnf install -y${MISSING_PKGS}" >&2
    echo "" >&2
    echo "(Not on Fedora? Install the equivalents with your package manager:" >&2
    echo "  Debian/Ubuntu: use ./install-ubuntu.sh instead," >&2
    echo "  or install python3, PyGObject (python3-gi), GTK 3, and WebKit2GTK 4.1.)" >&2
}

echo "[1/4] Checking dependencies..."
if ! command -v rpm >/dev/null 2>&1; then
    MISSING_PKGS=" $REQUIRED_PKGS"
    print_manual_instructions "'rpm' not found; this doesn't look like a Fedora/RHEL system, so dependencies can't be checked automatically."
    exit 1
fi

MISSING_PKGS=""
for pkg in $REQUIRED_PKGS; do
    if ! rpm -q "$pkg" >/dev/null 2>&1; then
        MISSING_PKGS="$MISSING_PKGS $pkg"
    fi
done

if [ -n "$MISSING_PKGS" ]; then
    echo "Missing packages:${MISSING_PKGS}"
    if ! command -v dnf >/dev/null 2>&1; then
        print_manual_instructions "'dnf' not found, so the missing packages can't be installed automatically."
        exit 1
    fi
    echo "Installing via dnf..."
    if ! $DNF_PREFIX dnf install -y $MISSING_PKGS; then
        print_manual_instructions "dnf failed to install the missing packages (no sudo access, no network, or the package is unavailable in your enabled repositories)."
        exit 1
    fi
    # dnf can exit 0 without installing everything (e.g. with --skip-broken
    # in dnf.conf), so verify rather than trust the exit status.
    STILL_MISSING=""
    for pkg in $MISSING_PKGS; do
        rpm -q "$pkg" >/dev/null 2>&1 || STILL_MISSING="$STILL_MISSING $pkg"
    done
    if [ -n "$STILL_MISSING" ]; then
        MISSING_PKGS="$STILL_MISSING"
        print_manual_instructions "these packages are still missing after dnf ran."
        exit 1
    fi
else
    echo "All dependencies already installed."
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
echo "=========================================="
