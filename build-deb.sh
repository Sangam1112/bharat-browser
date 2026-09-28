#!/bin/bash
# Build script to produce a .deb package for Ubuntu/Debian.
#
# Built by hand with ar/tar (the .deb format itself — see man 5 deb)
# instead of dpkg-deb, so it works on machines that don't have the dpkg
# tools installed (e.g. this repo's own Fedora dev machine). The output
# is a standard binary .deb either way; nothing about the resulting file
# format depends on which tool built it.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="1.2.37"
PKG_NAME="bharat-browser"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/Downloads}"
BUILD_ROOT="/tmp/deb_build_${PKG_NAME}"

echo "Building .deb package for ${PKG_NAME} v${VERSION}..."

rm -rf "$BUILD_ROOT"
mkdir -p "${BUILD_ROOT}/usr/share/bharat-browser/assets"
mkdir -p "${BUILD_ROOT}/usr/bin"
mkdir -p "${BUILD_ROOT}/usr/share/applications"
mkdir -p "${BUILD_ROOT}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${BUILD_ROOT}/DEBIAN"

cp bharat_browser.py "${BUILD_ROOT}/usr/share/bharat-browser/"
cp -r assets/* "${BUILD_ROOT}/usr/share/bharat-browser/assets/"
cp bharat-browser "${BUILD_ROOT}/usr/bin/bharat-browser"
cp bharat-browser.desktop "${BUILD_ROOT}/usr/share/applications/"
cp assets/bharat_icon.png "${BUILD_ROOT}/usr/share/icons/hicolor/256x256/apps/bharat-browser.png"

chmod +x "${BUILD_ROOT}/usr/bin/bharat-browser" "${BUILD_ROOT}/usr/share/bharat-browser/bharat_browser.py"

# Installed size in KiB, as the control file's Installed-Size field expects.
INSTALLED_SIZE_KB=$(du -sk "$BUILD_ROOT" --exclude="$BUILD_ROOT/DEBIAN" | cut -f1)

cat <<EOF > "${BUILD_ROOT}/DEBIAN/control"
Package: bharat-browser
Version: ${VERSION}
Section: web
Priority: optional
Architecture: all
Installed-Size: ${INSTALLED_SIZE_KB}
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0
Maintainer: Bharat Browser Developer <developer@bharatbrowser.org>
Homepage: https://github.com/Sangam1112/bharat-browser
Description: Modern, Ultra-Fast, and Privacy-First Web Browser
 Bharat Browser is a modern, high-performance web browser designed with
 strict security, privacy protection, and site compatibility at its core.
 GTK3 + WebKit2GTK desktop application with a custom ad/tracker blocklist,
 tracking-parameter stripping, HTTPS upgrading, and a DarkReader-style
 dark mode, all built in-house.
EOF

DEB_ROOT="/tmp/deb_pkg_${PKG_NAME}"
rm -rf "$DEB_ROOT"
mkdir -p "$DEB_ROOT"

echo "2.0" > "${DEB_ROOT}/debian-binary"

# --numeric-owner + --owner=0 --group=0: fake root:root ownership in the
# archive metadata without actually needing to be root to build this.
tar --numeric-owner --owner=0 --group=0 -czf "${DEB_ROOT}/control.tar.gz" -C "${BUILD_ROOT}/DEBIAN" .
tar --numeric-owner --owner=0 --group=0 -czf "${DEB_ROOT}/data.tar.gz" \
    -C "$BUILD_ROOT" --exclude="./DEBIAN" .

mkdir -p "$OUTPUT_DIR"
DEB_FILE="${OUTPUT_DIR}/bharat-browser_${VERSION}-1_all.deb"
rm -f "$DEB_FILE"

# Debian binary package format: an ar archive of exactly these three
# members, in this order (see `man 5 deb`).
(cd "$DEB_ROOT" && ar rc "$DEB_FILE" debian-binary control.tar.gz data.tar.gz)

echo "Built: ${DEB_FILE}"

# Copy into the repo root too, matching how build-rpm.sh's tarball lands
# next to the source when run with OUTPUT_DIR set to the repo itself.
if [ "$OUTPUT_DIR" != "$SCRIPT_DIR" ]; then
    cp "$DEB_FILE" "$SCRIPT_DIR/"
fi

rm -rf "$BUILD_ROOT" "$DEB_ROOT"
echo "Done."
