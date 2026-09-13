#!/bin/bash
# Build script to produce RPM package for Fedora / RHEL
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="1.2.4"
PKG_NAME="bharat-browser"
BUILD_ROOT="/tmp/rpm_build_${PKG_NAME}"

echo "Building RPM package for ${PKG_NAME} v${VERSION}..."

rm -rf "$BUILD_ROOT"
mkdir -p "${BUILD_ROOT}/usr/share/bharat-browser/assets"
mkdir -p "${BUILD_ROOT}/usr/bin"
mkdir -p "${BUILD_ROOT}/usr/share/applications"
mkdir -p "${BUILD_ROOT}/usr/share/icons/hicolor/256x256/apps"

cp bharat_browser.py "${BUILD_ROOT}/usr/share/bharat-browser/"
cp -r assets/* "${BUILD_ROOT}/usr/share/bharat-browser/assets/"
cp bharat-browser "${BUILD_ROOT}/usr/bin/bharat-browser"
cp bharat-browser.desktop "${BUILD_ROOT}/usr/share/applications/"
cp assets/bharat_icon.png "${BUILD_ROOT}/usr/share/icons/hicolor/256x256/apps/bharat-browser.png"

chmod +x "${BUILD_ROOT}/usr/bin/bharat-browser" "${BUILD_ROOT}/usr/share/bharat-browser/bharat_browser.py"

if command -v rpmbuild &> /dev/null; then
    mkdir -p ~/rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
    cat <<EOF > ~/rpmbuild/SPECS/bharat-browser.spec
Name:           bharat-browser
Version:        ${VERSION}
Release:        1%{?dist}
Summary:        Modern, Ultra-Fast, Multi-Tab, and Privacy-First Web Browser
License:        MIT
URL:            https://github.com/Sangam1112/bharat-browser
BuildArch:      noarch
Requires:       python3 python3-gobject gtk3 webkit2gtk4.1

%description
Bharat Browser is a modern, high-performance web browser designed with strict security, privacy protection, and site compatibility at its core.

%install
mkdir -p %{buildroot}
cp -r ${BUILD_ROOT}/* %{buildroot}/

%files
/usr/bin/bharat-browser
/usr/share/bharat-browser
/usr/share/applications/bharat-browser.desktop
/usr/share/icons/hicolor/256x256/apps/bharat-browser.png

%changelog
* Sun Sep 13 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.4-1
- Fedora Linux release support
EOF

    rpmbuild -bb ~/rpmbuild/SPECS/bharat-browser.spec
    cp ~/rpmbuild/RPMS/noarch/bharat-browser-${VERSION}-1*.noarch.rpm ./
    echo "RPM package generated: $(ls bharat-browser-${VERSION}-1*.noarch.rpm)"
else
    echo "rpmbuild not installed. Packaging standalone tarball for Fedora..."
    tar -czf "bharat-browser_${VERSION}_fedora.tar.gz" -C "$BUILD_ROOT" .
    echo "Archive generated: bharat-browser_${VERSION}_fedora.tar.gz"
fi
