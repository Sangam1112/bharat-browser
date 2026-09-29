#!/bin/bash
# Build script to produce RPM package and standalone release tarball for Fedora / RHEL
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="1.2.41"
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
* Wed Sep 30 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.41-1
- Fix dark mode on sites with a transparent page background (e.g.
  economictimes.indiatimes.com): the html background was set to #121212 but
  the invert filter also inverted it to light grey, leaving pale text on
  grey; it is now white so it inverts to dark
- Fix About text in Settings not rendering: a bare '&' in the Pango markup
  made GTK drop the label

* Wed Sep 30 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.40-1
- Fix crash on Wayland: a very long history URL made the URL-bar
  autocomplete popup wider than GDK's 32767px native window limit, so GTK
  dereferenced a NULL cairo surface and segfaulted. Autocomplete rows are
  now ellipsized (full URL/title still stored and matched)

* Tue Sep 29 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.39-1
- Fix Gtk.CssProvider leak: every new window (including private windows)
  added a duplicate process-wide CSS provider that was never removed;
  now loaded once per process
- Cache GPU detection at module scope instead of re-running glxinfo/lspci
  subprocess calls (each with its own timeout) on every window open
- Remove unused GdkPixbuf import

* Tue Sep 29 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.38-1
- Add GPU Acceleration toggle to Settings > Performance: detects the system
  GPU and lets the compositor, canvas, and WebGL render on it (lower RAM/CPU
  use) instead of falling back to software rendering, with an on/off switch

* Mon Sep 28 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.37-1
- Add "Clear browsing history on exit" privacy tick box feature (automatically
  clears browsing history and URL autocomplete cache on window close)
- Reorganize Settings dialog into modern categorized tabs (General, Privacy &
  Security, Performance & Advanced, Data & Actions) with clean card-based layouts
- Synchronize versions across .deb and .rpm release packages

* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.36-1
- Add background tab suspension: tabs inactive for 15+ minutes are
  automatically unloaded (WebKit session state saved via
  get_session_state()/restore_session_state(), then the tab is dropped to
  about:blank) to free WebProcess memory, and transparently restored —
  full back-forward history and exact page content intact — the instant
  the user switches back to that tab. Never touches the active tab, a
  tab currently loading, or a tab playing audio/video. Suspended tabs
  show a "💤" prefix on their label so the state is visible, and the
  underlying URL/title are preserved for session.json and the History
  Dashboard even while the live webview sits on about:blank. Verified
  live end-to-end (save -> suspend -> reactivate -> back-forward history
  and page content match exactly) before shipping, unlike the earlier
  single-process-mode request in Low Memory Mode — this feature's core
  mechanism is fully confirmed working, not just requested. Toggle at
  Settings > Advanced > "Suspend Inactive Background Tabs" (on by
  default).

* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.35-1
- Add a "Low Memory Mode" toggle in Settings > Advanced, built and verified
  after live monitoring showed a few ordinary tabs pushing memory past 1GB
  and reproducibly triggering silent process death on a 3.7GB-RAM test
  machine already under swap pressure. Immediately shrinks WebKit's page
  cache (WEB_BROWSER -> DOCUMENT_VIEWER cache model) with no restart
  needed. Also requests a single shared render process for all tabs after
  a restart, but empirical testing found WebKitGTK 2.54 does not honor
  that request (4 tabs still spawned 4 separate WebProcess instances with
  it set) — left in place for other WebKit versions but not advertised as
  a working effect, so the feature only promises what was actually
  verified to help
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.34-1
- Fix History Dashboard/autocomplete entries getting permanently stuck on
  a URL-filename fallback instead of the real page title for most
  first-time visits (get_title() is still empty at the exact moment the
  entry is first recorded; now self-corrects once the real title arrives,
  same as the tab label already did)
- Fix closing the main window silently destroying every open Private
  window mid-session with no warning; the app now only quits once every
  open window (main + private) has actually closed
- Fix "Restart Now" (after an update) silently dropping the active tab's
  in-progress browsing-time stats instead of saving them first
- Harden the History Dashboard's row-click navigation against a fragile
  inline onclick + string-interpolation pattern (was HTML-attribute-safe
  via GLib.markup_escape_text, but not JS-string-safe); now uses a
  data-url attribute plus a delegated listener instead
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.33-1
- Fix two real CPU/performance issues in the per-tab injected scripts,
  investigated after a user reported memory/system-overload issues on
  Ubuntu: (1) the link-prefetch mouseover handler ran a document.
  querySelector() DOM scan on every single hover event, even re-hovering
  the same link, and let <head> grow unboundedly on link-heavy pages —
  now uses an in-memory Set (no DOM query) and caps at 30 origins;
  (2) the media-polyfill's MutationObserver ran a full document.
  querySelectorAll('video') scan on every DOM mutation batch — confirmed
  via testing that independent page updates (live feeds, ad refreshes,
  chat widgets) each trigger a separate callback and separate full-page
  scan — now only inspects each mutation's newly-added nodes
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.32-1
- Fix opening .doc/.docx/.xls/.xlsx (and any other file type WebKit has no
  in-tab renderer for) showing a confusing "page didn't load" error after
  wasting a retry. WebKit has no rendering engine for Office formats
  (unlike PDF, which it renders natively) — these now download instead,
  same as clicking a download link would do, via a new decide-policy
  handler that converts any unsupported response into a real download
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.31-1
- PDF files now render in-tab (WebKit2GTK's built-in PDF.js-based viewer
  already supported this; verified nothing in the app interfered) and the
  tab/window title now falls back to the file's name instead of getting
  stuck on "New Tab" for PDFs and other titleless content
- Fix a pre-existing race: get_title() is still empty at the exact instant
  load-changed(FINISHED) fires (confirmed: WebKit sets the real title
  ~200ms later via notify::title, even for a trivial local page), so the
  tab label and window title could get stuck one step behind, or never
  correct themselves, until a tab switch happened to re-read it. Added a
  notify::title handler so the tab label and window title (and the
  history entry's recorded title) always reflect the real title once
  WebKit reports it, not just whatever was available at FINISHED
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.30-1
- Verified the .deb package with real 'apt install' runs inside actual
  Ubuntu 22.04 and 24.04 containers (podman): full dependency resolution,
  dpkg --verify passes, and the installed Python module imports cleanly
  with real GTK3/WebKit2GTK bindings present
- Fix stale "Ubuntu 24.04+ ships webkit2gtk 4.1; older releases only have
  4.0" comments in install-ubuntu.sh/install-wsl.sh and README: 4.1 was
  confirmed available on 22.04 too in real testing, so reworded to
  describe the actual fallback behavior instead of a version cutoff
  that turned out to be inaccurate
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.29-1
- Add build-deb.sh and a shipped .deb package (bharat-browser_1.2.29-1_all.deb)
  for native 'sudo apt install ./file.deb' installation on Ubuntu/Debian,
  in addition to the existing install-ubuntu.sh script. Built by hand with
  ar/tar rather than dpkg-deb so it doesn't require Debian packaging tools
  on the (Fedora) build machine
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.28-1
- Hide the tab strip entirely when only one tab is open, and show it
  again as soon as a second tab is opened, instead of always showing a
  single-tab bar with nothing useful to switch between
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.27-1
- Fix install-ubuntu.sh unconditionally prefixing apt-get with sudo even
  when already running as root, which fails with "sudo: command not
  found" on minimal root-only Ubuntu container images that don't ship
  sudo at all
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.26-1
- Add install-ubuntu.sh: a first-class native Ubuntu/Debian installer
  (apt dependencies, user or --system install, desktop shortcut + icon
  registration), instead of only having Fedora RPM/tarball installers
  and the WSL-on-Windows script with no native-Linux Ubuntu path
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.25-1
- Add a first-run greeting ("नमस्ते <login name> 👋") shown once ever, on
  the very first startup, auto-hiding after 2 seconds. Never shown in
  private windows, and a private window never consumes/marks the
  one-time flag either, so the greeting still appears the first time a
  normal window is opened even if a private window ran first
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.24-1
- Remove the "Bharat vX.Y.Z" brand badge that sat next to the Back/
  Forward/Reload buttons in the toolbar, freeing up address-bar space
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.23-1
- Add a browsing history dashboard: Ctrl+H or Settings > Actions > "Show
  History" opens a new tab ranking visited sites by time spent, with
  visit counts and last-visited timestamps
- Time-on-page is now tracked per site (from page load to navigating
  away, closing the tab, or closing the window) and persisted alongside
  existing URL-bar autocomplete history; never tracked in private windows
- "Clear Browsing History & Cookies" now also clears this history data,
  not just WebKit's cache/cookies as before
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.22-1
- Add graceful handling for network-level load failures (e.g. "Peer failed
  to perform TLS handshake: Connection reset by peer"), previously shown
  to the user as a raw, unstyled error with no automatic recovery. Many of
  these are transient and succeed on a plain retry, so the browser now
  retries once automatically before giving up; if it still fails, a
  friendly branded error page is shown instead of raw GLib error text.
- Fixed a real bug found while testing the above: WebKit fires
  load-changed(FINISHED) even for a load that just failed, which was
  unconditionally clearing the new retry counter every time and would
  have caused a silent infinite retry loop that never reaches the
  "show an error page" fallback. Now only cleared on genuine success.
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.21-1
- Add Find in Page (Ctrl+F): a floating search bar with live match-count,
  next/previous (Enter / Shift+Enter), and Escape to close, backed by
  WebKit's native find controller
- Add URL bar autocomplete from browsing history (url + title), with
  substring matching and click/Enter-to-navigate; never recorded or read
  for private windows
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.20-1
- Add a "Open homepage on startup" Settings toggle (off by default). Since
  the browser restores your previous session on launch, setting a custom
  homepage previously had no effect on startup, only on new tabs; this
  toggle lets homepage win over session restore when turned on
- Fix normal mouse-wheel/touchpad page scrolling being completely broken
  by the v1.2.18 Ctrl+scroll-zoom fix: a Gtk.EventControllerScroll attached
  directly to the webview (in either CAPTURE or BUBBLE phase) fully claims
  scroll input at the GTK controller-framework level, which is mutually
  exclusive with WebKit's own native page-scroll handling. Reverted to a
  plain "scroll-event" signal connection (with SCROLL_MASK/SMOOTH_SCROLL_MASK
  requested via add_events), which doesn't have that problem: returning
  False lets the event continue on to WebKit's normal scroll handling.
  Re-verified both normal scrolling and Ctrl+scroll zoom against live
  synthetic GDK events after the fix.
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.18-1
- Fix Ctrl+scroll zoom not actually working: it was wired up via a plain
  "scroll-event" GTK signal, but WebKitWebView manages its own native
  input surface for page scrolling and can consume/ignore those events
  before that signal ever fires for real hardware input. Replaced with a
  Gtk.EventControllerScroll in the CAPTURE propagation phase, which
  reliably intercepts the event before WebKit's own handling of it.
* Sun Sep 27 2026 Bharat Browser Developer <developer@bharatbrowser.org> - 1.2.17-1
- Add Ctrl+scroll wheel to zoom webpage content in/out (in addition to
  the existing Ctrl+/Ctrl-/Ctrl+0 keyboard shortcuts), including smooth
  scrolling (touchpad) support
- Add a floating on-screen zoom-percentage indicator shown for 2 seconds
  after every zoom change (keyboard or Ctrl+scroll), instead of only a
  statusbar message
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
