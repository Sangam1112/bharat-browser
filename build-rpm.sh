#!/bin/bash
# Build script to produce RPM package and standalone release tarball for Fedora / RHEL
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="1.2.16"
PKG_NAME="bharat-browser"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/Downloads}"
BUILD_ROOT="/tmp/rpm_build_${PKG_NAME}"

echo "Building package for ${PKG_NAME} v${VERSION}..."

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
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.16-1
- Fix Settings dialog scrolling being effectively unusable: the scroll area
  was packed with content_area.add(), which does not give it expand/fill,
  so it only ever got ~46px of height with the rest of the dialog left
  empty below it. Packed explicitly with expand=True, fill=True instead.
- Redesign Settings dialog sections as titled, bordered groups (General /
  Privacy & Security / Advanced / Actions) instead of a bare bold label,
  and grow the default dialog size so most setups need little to no
  scrolling to see everything
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.15-1
- Add a Default Search Engine setting (Google, Bing, DuckDuckGo, Yahoo) used
  by the address bar for non-URL input
- Add a Homepage setting: opened by new tabs, the New Tab button, and when
  the last tab closes; supports a custom address with input validation
  (non-http(s) schemes like javascript:/data:/file: are rejected)
- Redesign the Settings dialog into General / Privacy & Security / Advanced
  / Actions sections with tooltips and a scrollable layout, for readability
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.14-1
- Fix screenshot feature failing to save: WebKit's page snapshot carries an
  alpha channel, and some gdk-pixbuf JPEG backends (e.g. glycin on newer
  Fedora/GNOME) refuse to encode RGBA as JPEG; the snapshot is now flattened
  onto an opaque background before saving
- Fix links that open in a new tab/window (target="_blank", window.open(),
  middle-click, OAuth/"Sign in with..." popups) silently doing nothing;
  the browser now handles WebKit's "create" signal and opens them in a new
  tab in the same window
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.13-1
- Fix Settings/Downloads dialogs still showing a light system-themed title
  bar despite the dark content area fix in 1.2.12: they now get the same
  custom dark Gtk.HeaderBar the main window uses
- Fix header/title-bar text color (main window and dialogs) not being
  explicitly set to white, which some system GTK themes rendered as
  low-contrast dark-on-dark text
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.12-1
- Version-check overlay ("browser is up to date") now auto-dismisses after
  2 seconds instead of 5
- Settings, Downloads, and message dialogs now render in the app's dark
  theme instead of falling back to the light system GTK theme
- Add Ctrl+/Ctrl- to zoom webpage content in/out, and Ctrl+0 to reset zoom
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.11-1
- Fix URL bar misclassifying single-word LAN hostnames (e.g. "router") as
  search queries instead of URLs; local hostnames now correctly get http://
- Fix version-comparison helper mangling pre-release suffixes (e.g.
  "1.2.10-rc1") into bogus numbers instead of ignoring them
- Fix screenshot filenames silently overwriting when two shots are taken in
  the same second; now collision-safe like downloads
- Settings and session files are now written with 0600 permissions from
  creation instead of default umask-dependent permissions
- Add explicit TLS certificate-error handling with no click-through bypass,
  consistent with this browser's HTTPS-enforcement guarantee
- Fix statusbar auto-hide timers stacking on rapid status updates, which
  could hide the bar out from under a newer message
- build-rpm.sh no longer hardcodes a developer's home directory for output
* Sat Sep 26 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.10-1
- Performance/memory tuning: media-polyfill's per-tab 1500ms DOM poll loop
  replaced with a MutationObserver (removes a permanent per-tab CPU wakeup)
- The three injected UserScripts (media polyfill, anti-fingerprinting,
  prefetch) are now built once and shared across all tabs instead of being
  re-allocated on every single new tab
- React to OS low-memory-warning signals by trimming WebKit's cache instead
  of only ever growing it for the process lifetime
- Tried WEBKIT_HARDWARE_ACCELERATION_POLICY_ON_DEMAND for per-tab GPU memory
  savings; reverted after testing showed current WebKitGTK treats it as
  deprecated and identical to ALWAYS (logged a warning, changed nothing)
* Sat Sep 26 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.9-1
- Security/privacy audit fixes: updater now pins to a release tag and verifies
  a published sha256 checksum (was fetching mutable master with no integrity
  check), and caps the download size
- Native WKContentRuleList ad/tracker blocking added alongside the existing
  Python-level blocking; shared, expanded tracker domain list used by both
  the fallback path and the optional adblockparser engine (previously the
  "advanced" path used the same tiny 11-domain list as the fallback)
- Autoplay now requires a user gesture (was forced on for all sites)
- WebRTC is off by default (opt-in in Settings) to avoid local-IP leaks via
  ICE candidates; added an explicit allow/deny prompt for camera, mic,
  location, and notification permission requests
- HTTPS auto-upgrade now exempts LAN/private-IP hosts and bare local
  hostnames instead of only localhost/127.0.0.1
- Crashed tabs stop auto-reloading after repeated crashes instead of looping
  forever
- Added Private Browsing windows (Ctrl+Shift+N): ephemeral cookies/storage,
  no session-file persistence
- Extended anti-fingerprinting canvas/WebGL patch to WebGL2RenderingContext
* Sat Sep 26 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.8-1
- Add real self-updater: checks package.json on GitHub, downloads and installs
  the latest bharat_browser.py in place when writable, with a Restart Now
  button to apply it (falls back to a notify-only message when not writable)
* Sat Sep 26 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.7-1
- Replace OS-drawn titlebar with a slim custom dark titlebar (was a large light-themed strip eating vertical space)
- Tighten top bar margins, tab padding, and status bar padding for more vertical browsing space
* Fri Sep 25 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.5-1
- Fix tab-lookup attribute mangling bug breaking Back/Forward/Reload/session-restore/dark-mode
- Fix anti-fingerprinting script to inject before page scripts run
- Fix ad-block streaming exemption to match hostname instead of raw substring
- Downloads now saved to XDG Downloads dir with collision-safe filenames
- Add optional Developer Tools toggle (off by default)
- Modernized UI: flat theme, segmented nav controls, URL bar security icon
* Sun Sep 13 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.4-1
- Fedora Linux release support
EOF

    rpmbuild -bb ~/rpmbuild/SPECS/bharat-browser.spec
    cp ~/rpmbuild/RPMS/noarch/bharat-browser-${VERSION}-1*.noarch.rpm ./
fi

# Package standalone tarball for Fedora (includes install-fedora.sh, unlike the RPM buildroot)
cp install-fedora.sh "${BUILD_ROOT}/install-fedora.sh"
chmod +x "${BUILD_ROOT}/install-fedora.sh"
tar -czf "bharat-browser_${VERSION}_fedora.tar.gz" -C "$BUILD_ROOT" .
mkdir -p "$OUTPUT_DIR"
cp "bharat-browser_${VERSION}_fedora.tar.gz" "$OUTPUT_DIR/bharat-browser_${VERSION}_fedora.tar.gz"
echo "Archive generated: bharat-browser_${VERSION}_fedora.tar.gz (copied to $OUTPUT_DIR)"
