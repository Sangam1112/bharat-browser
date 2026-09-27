#!/usr/bin/env python3
"""
Bharat Browser v1.2.30 - GTK3 / WebKit2 Python Application
Modern, Ultra-Fast, Multi-Tab, and Privacy-First Web Browser engineered for Linux (Ubuntu)
"""
import sys
import os

# Enable GPU Hardware Acceleration & System-Level Acceleration Flags
os.environ["WEBKIT_FORCE_COMPOSITING_MODE"] = "1"
os.environ["GST_VAAPI_ALL_DRIVERS"] = "1"
os.environ["GST_DEBUG"] = "0"
os.environ["WEBKIT_USE_SINGLE_WEB_PROCESS"] = "0"

import ast
import getpass
import hashlib
import ipaddress
import re
import json
import time
import threading
import subprocess
import urllib.parse
import urllib.request
import gi
import cairo

gi.require_version('Gtk', '3.0')
try:
    gi.require_version('WebKit2', '4.1')
except ValueError:
    gi.require_version('WebKit2', '4.0')

from gi.repository import Gtk, Gdk, WebKit2, GLib, Gio, GdkPixbuf

# Import high-rating open-source ad-blocking engine (adblockparser) if available
for adblock_path in [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "adblockparser"),
    os.path.expanduser("~/.local/share/bharat-browser/adblockparser"),
    "/usr/share/bharat-browser/adblockparser",
]:
    if os.path.exists(adblock_path):
        sys.path.insert(0, adblock_path)
        break
# -------------------------------------------------------------------------
# 2-Stage Request Interceptor Filter (O(1) Domain Set Pre-lookup + Regex)
# Maintains Throughput > 25,000 requests/sec
#
# Single source of truth: both the adblockparser-backed engine below and the
# plain-Python fallback path use this same list, so having the optional
# adblockparser dependency installed no longer buys strictly identical (and
# previously much smaller) coverage to the fallback.
# -------------------------------------------------------------------------
BLOCKED_DOMAINS = {
    # Ad networks / exchanges
    'doubleclick.net', 'googlesyndication.com', 'googleadservices.com',
    'adservice.google.com', 'adservice.google.co.in', 'google-analytics.com',
    'googletagservices.com', 'adnxs.com',
    'adsrvr.org', 'rubiconproject.com', 'pubmatic.com', 'casalemedia.com',
    'openx.net', 'contextweb.com', 'media-ad.net', 'adroll.com',
    'criteo.com', 'criteo.net', 'taboola.com', 'outbrain.com',
    'amazon-adsystem.com', 'advertising.com', 'yieldmo.com', 'sharethrough.com',
    'smartadserver.com', 'adform.net', 'bidswitch.net', 'indexww.com',
    'sovrn.com', 'gumgum.com', 'medianet.com', 'mediavine.com',
    'revcontent.com', 'mgid.com', 'moatads.com', 'adtechus.com',
    'adcolony.com', 'applovin.com', 'unityads.unity3d.com', 'vungle.com',
    'chartboost.com', 'inmobi.com', 'mopub.com', 'flurry.com',

    # Social widget / tracking pixels
    'facebook.net', 'connect.facebook.net', 'ads.linkedin.com',
    'ads-twitter.com', 'analytics.twitter.com', 'static.ads-twitter.com',
    'ct.pinterest.com', 'analytics.snapchat.com', 'analytics.tiktok.com',

    # Web analytics / session replay / crash & perf telemetry
    'scorecardresearch.com', 'quantserve.com', 'quantcount.com',
    'hotjar.com', 'mixpanel.com', 'segment.io', 'segment.com',
    'amplitude.com', 'fullstory.com', 'mouseflow.com', 'crazyegg.com',
    'clicktale.net', 'clarity.ms', 'newrelic.com', 'nr-data.net',
    'bugsnag.com', 'sentry.io', 'rollbar.com', 'appdynamics.com',
    'chartbeat.com', 'comscore.com', 'krxd.net', 'demdex.net',
    'omtrdc.net', 'adobedtm.com', 'branch.io', 'app-measurement.com',
}

try:
    from adblockparser import AdblockRules
    ADBLOCK_ENGINE = AdblockRules([f"||{domain}^" for domain in sorted(BLOCKED_DOMAINS)])
except Exception:
    ADBLOCK_ENGINE = None

BLOCKED_REGEX = re.compile(
    r'(?:/adserver/|/ads/|/pagead/|/pixel\.gif|/tracker\.js|/telemetry|/analytics\.js|/gtm\.js|/collect\?|/log_event)',
    re.IGNORECASE
)

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'msclkid', 'mc_eid', 'yclid', '_openstat', 'igshid'
}

STREAMING_EXEMPT_DOMAINS = {
    'youtube.com', 'googlevideo.com', 'ytimg.com', 'youtube-nocookie.com',
    'ggpht.com', 'googleapis.com', 'gstatic.com', 'vimeocdn.com', 'vimeo.com',
    'twitch.tv', 'ttvnw.net', 'jtvnw.net'
}

STREAMING_EXEMPT_EXTENSIONS = ('.m3u8', '.mpd')

LOCAL_HOST_SUFFIXES = ('.local', '.lan', '.home', '.internal', '.localdomain')

def is_local_network_host(host):
    """True for LAN/loopback addresses and bare local hostnames, which usually
    only serve plain HTTP (routers, printers, IoT devices, dev servers) and have
    no real HTTPS certificate to upgrade to."""
    if not host:
        return False
    if host == "localhost" or host.endswith(LOCAL_HOST_SUFFIXES):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    # A bare single-label hostname (no dots) is almost always a LAN device,
    # never a real publicly-routable, certificate-bearing FQDN.
    return '.' not in host

def _host_matches_domain_set(host, domain_set):
    if host in domain_set:
        return True
    return any(host.endswith('.' + dom) for dom in domain_set)

def is_ad_or_tracker(url_str):
    try:
        parsed = urllib.parse.urlparse(url_str)
        host = (parsed.hostname or '').lower()
        if not host:
            return False

        # Exempt YouTube / GoogleVideo streaming domains and manifest files from cancellation
        if _host_matches_domain_set(host, STREAMING_EXEMPT_DOMAINS):
            return False
        if parsed.path.lower().endswith(STREAMING_EXEMPT_EXTENSIONS):
            return False

        if ADBLOCK_ENGINE is not None:
            try:
                return ADBLOCK_ENGINE.should_block(url_str)
            except Exception:
                pass

        # Everything below only runs when the optional `adblockparser`
        # dependency isn't installed, or it raised above: a plain-Python
        # fallback over the same BLOCKED_DOMAINS/BLOCKED_REGEX data, not a
        # second, independent blocking tier.
        # Stage 1: O(1) Domain Set Pre-lookup
        if host in BLOCKED_DOMAINS:
            return True
        parts = host.rsplit('.', 2)
        if len(parts) >= 2:
            root_domain = parts[-2] + '.' + parts[-1]
            if root_domain in BLOCKED_DOMAINS:
                return True
        if len(parts) >= 3:
            sub_domain = parts[-3] + '.' + parts[-2] + '.' + parts[-1]
            if sub_domain in BLOCKED_DOMAINS:
                return True

        # Stage 2: Path Regex Matching
        if BLOCKED_REGEX.search(parsed.path):
            return True
    except Exception:
        pass
    return False

def build_content_blocker_rules_json():
    """Compile BLOCKED_DOMAINS into a WKContentRuleList (WebKit's native,
    network-level content blocker). This runs inside the web process itself
    instead of round-tripping every subresource through a Python callback, so
    it's faster and isn't dependent on request-mutation semantics of the
    resource-load-started signal working the same way across WebKitGTK
    versions. It's additive: the existing Python-level blocking in
    on_resource_load_started stays in place unchanged as a second layer."""
    # WebKit's content-extension regex engine doesn't support alternation
    # ("Disjunctions are not supported yet"), so each domain gets two plain
    # anchored patterns instead of one pattern with a `(sep|$)` alternation.
    rules = []
    for domain in sorted(BLOCKED_DOMAINS):
        prefix = f"^https?://([a-z0-9-]+\\.)*{re.escape(domain)}"
        rules.append({"trigger": {"url-filter": prefix + "[:/]"}, "action": {"type": "block"}})
        rules.append({"trigger": {"url-filter": prefix + "$"}, "action": {"type": "block"}})
    return json.dumps(rules).encode("utf-8")

def sanitize_url(url_str):
    if '?' not in url_str:
        return url_str
    try:
        parsed = urllib.parse.urlparse(url_str)
        if not parsed.query:
            return url_str
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_pairs = [(k, v) for k, v in query_pairs if k.lower() not in TRACKING_PARAMS]
        if len(filtered_pairs) == len(query_pairs):
            return url_str
        new_query = urllib.parse.urlencode(filtered_pairs)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception:
        return url_str

CONFIG_DIR = os.path.expanduser("~/.config/bharat-browser")
CACHE_DIR = os.path.expanduser("~/.cache/bharat-browser")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")
HISTORY_FILE = os.path.join(CONFIG_DIR, "history.json")
HISTORY_MAX_ENTRIES = 500

SEARCH_ENGINES = {
    "Google": "https://www.google.com/search?q={query}",
    "Bing": "https://www.bing.com/search?q={query}",
    "DuckDuckGo": "https://duckduckgo.com/?q={query}",
    "Yahoo": "https://search.yahoo.com/search?p={query}",
}
DEFAULT_SEARCH_ENGINE = "Google"
DEFAULT_HOMEPAGE = "https://www.google.com"


def sanitize_homepage_url(url_str):
    """Normalize/validate a user-supplied homepage URL. Rejects non-http(s)
    schemes (javascript:, data:, file:, etc.) so a tampered or malformed
    settings.json can't turn "every new tab" into a local code-execution or
    disk-read primitive; falls back to DEFAULT_HOMEPAGE on anything invalid."""
    if not url_str:
        return DEFAULT_HOMEPAGE
    candidate = url_str.strip()
    if not candidate:
        return DEFAULT_HOMEPAGE
    parsed = urllib.parse.urlparse(candidate)
    # Reject any explicit non-http(s) scheme (javascript:, data:, file:, etc.)
    # up front, before the "no scheme -> assume bare domain" branch below can
    # accidentally treat "javascript:alert(1)" as a hostname to prefix.
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return DEFAULT_HOMEPAGE
    if not parsed.scheme:
        candidate = "https://" + candidate
        parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return DEFAULT_HOMEPAGE
    return candidate

def load_persistent_settings():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("Settings load note:", e)
    return {}

def _write_json_private(path, data):
    """Write JSON with 0600 permissions from creation, not applied after the
    fact, so browsing history/session data is never briefly world/group
    readable and isn't left exposed if a chmod step were skipped."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def save_persistent_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        _write_json_private(CONFIG_FILE, settings)
    except Exception as e:
        print("Settings save note:", e)

def load_session_state():
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("Session load note:", e)
    return {}

def save_session_state(urls):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temp_file = SESSION_FILE + ".tmp"
        _write_json_private(temp_file, {"urls": urls, "timestamp": time.time()})
        os.replace(temp_file, SESSION_FILE)
    except Exception as e:
        print("Session save note:", e)

def load_url_history():
    """Returns a list of {"url":..., "title":...} dicts, most-recent first.
    Never called for private windows, matching the session-state pattern."""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        print("History load note:", e)
    return []

def save_url_history(entries):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temp_file = HISTORY_FILE + ".tmp"
        _write_json_private(temp_file, entries)
        os.replace(temp_file, HISTORY_FILE)
    except Exception as e:
        print("History save note:", e)

# Anti-Fingerprinting Farbling Engine JS
# Registered as a UserScript with START injection time so it patches
# canvas/WebGL/navigator APIs before any page script can read the originals.
FARBLING_JS = """
(function() {
    if (window.__bharat_farbling__) return;
    window.__bharat_farbling__ = true;
    if (window.location.hostname.includes('youtube.com') || window.location.hostname.includes('googlevideo.com')) return;

    try {
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function() {
            const res = origGetImageData.apply(this, arguments);
            if (res && res.data && res.data.length > 0) {
                res.data[0] = res.data[0] ^ 1;
            }
            return res;
        };
    } catch(e){}

    try {
        const getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return "Generic Open-Source GPU Engine";
            if (param === 37446) return "Bharat Privacy WebGL Renderer";
            return getParam.apply(this, arguments);
        };
    } catch(e){}

    try {
        if (window.WebGL2RenderingContext) {
            const getParam2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return "Generic Open-Source GPU Engine";
                if (param === 37446) return "Bharat Privacy WebGL Renderer";
                return getParam2.apply(this, arguments);
            };
        }
    } catch(e){}

    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'doNotTrack', { get: () => "1" });
    } catch(e){}
})();
"""

# Smart Link Prefetching UserScript JS
PREFETCH_USER_SCRIPT = """
(function() {
    if (window.__bharat_prefetch_listener__) return;
    window.__bharat_prefetch_listener__ = true;

    function prefetchLink(e) {
        try {
            let target = e.target;
            while (target && target.tagName !== 'A') {
                target = target.parentElement;
            }
            if (target && target.href && target.href.startsWith('http')) {
                const url = new URL(target.href);
                const origin = url.origin;
                if (!document.querySelector(`link[rel="dns-prefetch"][href="${origin}"]`)) {
                    const dnsLink = document.createElement('link');
                    dnsLink.rel = 'dns-prefetch';
                    dnsLink.href = origin;
                    document.head.appendChild(dnsLink);

                    const connLink = document.createElement('link');
                    connLink.rel = 'preconnect';
                    connLink.href = origin;
                    document.head.appendChild(connLink);
                }
            }
        } catch(err){}
    }

    document.addEventListener('mouseover', prefetchLink, { passive: true });
})();
"""

DARKREADER_CSS = """
html {
    filter: invert(90%) hue-rotate(180deg) !important;
    background-color: #121212 !important;
}
img, video, canvas, svg, [style*="background-image"] {
    filter: invert(111%) hue-rotate(180deg) !important;
}
"""

MEDIA_POLYFILL_JS = """
(function() {
    if (window.__bharat_media_polyfill__) return;
    window.__bharat_media_polyfill__ = true;

    if (window.MediaSource && window.MediaSource.isTypeSupported) {
        const origIsTypeSupported = window.MediaSource.isTypeSupported;
        window.MediaSource.isTypeSupported = function(mimeType) {
            if (!mimeType) return false;
            const str = mimeType.toLowerCase();
            if (str.includes('av01')) return false;
            if (str.includes('opus') || str.includes('mp4a') || str.includes('vorbis') || str.includes('vp9') || str.includes('vp8') || str.includes('vp09') || str.includes('avc1') || str.includes('webm') || str.includes('mp4') || str.includes('h264')) {
                return true;
            }
            return origIsTypeSupported.call(window.MediaSource, mimeType);
        };
    }

    if (window.HTMLMediaElement && window.HTMLMediaElement.prototype.canPlayType) {
        const origCanPlay = window.HTMLMediaElement.prototype.canPlayType;
        window.HTMLMediaElement.prototype.canPlayType = function(type) {
            if (!type) return '';
            const str = type.toLowerCase();
            if (str.includes('av01')) return '';
            if (str.includes('opus') || str.includes('mp4a') || str.includes('vorbis') || str.includes('vp9') || str.includes('vp8') || str.includes('vp09') || str.includes('avc1') || str.includes('webm') || str.includes('mp4') || str.includes('h264')) {
                return 'probably';
            }
            return origCanPlay.call(this, type);
        };
    }

    if (navigator.mediaCapabilities && navigator.mediaCapabilities.decodingInfo) {
        const origDecInfo = navigator.mediaCapabilities.decodingInfo;
        navigator.mediaCapabilities.decodingInfo = function(configuration) {
            if (configuration && configuration.video && configuration.video.contentType) {
                const type = configuration.video.contentType.toLowerCase();
                if (type.includes('av01')) {
                    return Promise.resolve({ supported: false, smooth: false, powerEfficient: false });
                }
            }
            return origDecInfo.call(this, configuration).then(function(result) {
                return {
                    supported: (result && result.supported !== undefined) ? result.supported : true,
                    smooth: true,
                    powerEfficient: true
                };
            }).catch(function() {
                return { supported: true, smooth: true, powerEfficient: true };
            });
        };
    }

    function optimizeVideoElements() {
        try {
            const videos = document.querySelectorAll('video');
            videos.forEach(v => {
                if (!v.__bharat_boosted__) {
                    v.__bharat_boosted__ = true;
                    v.preload = 'auto';
                }
            });
        } catch(e){}
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', optimizeVideoElements);
    } else {
        optimizeVideoElements();
    }

    // Event-driven instead of a 1500ms poll loop running forever in every
    // open tab: react only when the DOM actually changes (new <video> added
    // by the page's own JS/SPA routing), which is the only time there's
    // new work to do anyway.
    try {
        const observer = new MutationObserver(optimizeVideoElements);
        observer.observe(document.documentElement || document, { childList: true, subtree: true });
    } catch(e){}
})();
"""

class BharatBrowserWindow(Gtk.Window):
    def __init__(self, private=False):
        self.current_version = "1.2.30"
        self.is_private = private
        title_suffix = " (Private)" if private else ""
        super().__init__(title=f"Bharat Browser v{self.current_version}{title_suffix}")
        self._private_windows = []
        self.set_default_size(1280, 850)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Slim custom titlebar (replaces the OS-drawn titlebar, which reserved
        # a large light-themed strip for the page title and ate vertical space)
        titlebar = Gtk.HeaderBar()
        titlebar.set_show_close_button(True)
        titlebar.set_title("")
        titlebar.get_style_context().add_class("bharat-titlebar")
        self.set_titlebar(titlebar)

        self.icon_path = None
        icon_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bharat_icon.png"),
            "/usr/share/bharat-browser/assets/bharat_icon.png",
            "/usr/share/icons/hicolor/256x256/apps/bharat-browser.png",
            "/usr/share/icons/hicolor/128x128/apps/bharat-browser.png",
            os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/bharat-browser.png"),
            os.path.expanduser("~/.local/share/icons/bharat-browser.png"),
            os.path.expanduser("~/.local/share/bharat-browser/assets/bharat_icon.png")
        ]
        for candidate in icon_candidates:
            if os.path.exists(candidate):
                self.icon_path = candidate
                try:
                    self.set_icon_from_file(candidate)
                    break
                except Exception as e:
                    print("Icon load note:", e)

        self.blocked_count = 0
        self.downloads_history = []
        self._crash_counts = {}
        self._load_failure_counts = {}
        self._page_view_start = {}

        saved_settings = load_persistent_settings()
        self.dark_mode_active = saved_settings.get("dark_mode", False)
        self.adblock_enabled = saved_settings.get("adblock_enabled", True)
        self.clearurls_enabled = saved_settings.get("clearurls_enabled", True)
        self.https_enabled = saved_settings.get("https_enabled", True)
        self.dev_tools_enabled = saved_settings.get("dev_tools_enabled", False)
        self.webrtc_enabled = saved_settings.get("webrtc_enabled", False)
        self.search_engine = saved_settings.get("search_engine", DEFAULT_SEARCH_ENGINE)
        if self.search_engine not in SEARCH_ENGINES:
            self.search_engine = DEFAULT_SEARCH_ENGINE
        self.homepage = sanitize_homepage_url(saved_settings.get("homepage", DEFAULT_HOMEPAGE))
        self.open_homepage_on_startup = saved_settings.get("open_homepage_on_startup", False)
        self.first_run_greeted = saved_settings.get("first_run_greeted", False)

        # URL-bar autocomplete history. Never loaded/written for private
        # windows, matching the session-state privacy guarantee.
        self.url_history = [] if self.is_private else load_url_history()

        self.apply_custom_css()

        # WebKit DataManager & WebContext for high performance caching
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        if self.is_private and hasattr(WebKit2.WebsiteDataManager, 'new_ephemeral'):
            # Ephemeral manager: cookies/cache/storage live only in memory for
            # this window's lifetime and are never written to disk.
            self.data_mgr = WebKit2.WebsiteDataManager.new_ephemeral()
            self.context = WebKit2.WebContext.new_with_website_data_manager(self.data_mgr)
        elif hasattr(WebKit2, 'WebsiteDataManager'):
            self.data_mgr = WebKit2.WebsiteDataManager(
                base_cache_directory=CACHE_DIR,
                base_data_directory=CONFIG_DIR
            )
            self.context = WebKit2.WebContext.new_with_website_data_manager(self.data_mgr)
        else:
            self.context = WebKit2.WebContext.get_default()

        if hasattr(WebKit2, 'CacheModel') and hasattr(WebKit2.CacheModel, 'WEB_BROWSER'):
            self.context.set_cache_model(WebKit2.CacheModel.WEB_BROWSER)
        self.context.connect("download-started", self.on_download_started)

        # WebKit Settings Optimization
        self.web_settings = WebKit2.Settings()
        self.web_settings.set_enable_developer_extras(self.dev_tools_enabled)
        # WebRTC is off by default: RTCPeerConnection can leak a machine's local
        # (and behind some NATs, public) IP via ICE candidates even without any
        # getUserMedia permission grant, which defeats VPN/privacy expectations.
        # Users who need camera/mic/video-calling can opt in from Settings.
        self.web_settings.set_enable_webrtc(self.webrtc_enabled)
        self.web_settings.set_enable_media_stream(self.webrtc_enabled)
        self.web_settings.set_enable_javascript(True)
        self.web_settings.set_enable_media(True)
        self.web_settings.set_enable_mediasource(True)
        self.web_settings.set_enable_media_capabilities(True)
        self.web_settings.set_enable_encrypted_media(True)
        self.web_settings.set_media_playback_allows_inline(True)
        self.web_settings.set_media_playback_requires_user_gesture(True)
        # NOTE: WEBKIT_HARDWARE_ACCELERATION_POLICY_ON_DEMAND was tried here to
        # avoid paying for a GPU-composited layer tree on tabs that don't need
        # one, but current WebKitGTK (2.4x+) has deprecated it and treats it as
        # identical to ALWAYS (logs a warning and changes nothing), so there's
        # no real per-tab saving available at this settings layer today.
        self.web_settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
        if hasattr(self.web_settings, 'set_enable_smooth_scrolling'):
            self.web_settings.set_enable_smooth_scrolling(True)
        self.web_settings.set_enable_html5_database(True)
        self.web_settings.set_enable_html5_local_storage(True)
        self.web_settings.set_user_agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

        # Main Overlay
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.overlay.add(main_vbox)

        # Top Navigation Bar
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top_bar.get_style_context().add_class("top-bar")
        top_bar.set_margin_start(10)
        top_bar.set_margin_end(10)
        top_bar.set_margin_top(3)
        top_bar.set_margin_bottom(3)
        main_vbox.pack_start(top_bar, False, False, 0)

        # Nav Buttons (grouped as a segmented control)
        nav_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav_group.get_style_context().add_class("nav-group")

        self.btn_back = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.btn_back.get_style_context().add_class("flat-icon-btn")
        self.btn_back.set_tooltip_text("Back")
        self.btn_back.connect("clicked", self.on_back_clicked)
        nav_group.pack_start(self.btn_back, False, False, 0)

        self.btn_forward = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self.btn_forward.get_style_context().add_class("flat-icon-btn")
        self.btn_forward.set_tooltip_text("Forward")
        self.btn_forward.connect("clicked", self.on_forward_clicked)
        nav_group.pack_start(self.btn_forward, False, False, 0)

        self.btn_reload = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.btn_reload.get_style_context().add_class("flat-icon-btn")
        self.btn_reload.set_tooltip_text("Reload Page")
        self.btn_reload.connect("clicked", self.on_reload_clicked)
        nav_group.pack_start(self.btn_reload, False, False, 0)

        top_bar.pack_start(nav_group, False, False, 0)

        # URL Entry with dynamic security icon
        self.url_entry = Gtk.Entry()
        self.url_entry.get_style_context().add_class("url-entry")
        self.url_entry.set_placeholder_text("Search Google or enter URL...")
        self.url_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "channel-insecure-symbolic")
        self.url_entry.connect("activate", self.on_url_activate)
        top_bar.pack_start(self.url_entry, True, True, 4)

        # Autocomplete from browsing history (url, title)
        self.url_completion_store = Gtk.ListStore(str, str)
        for entry in self.url_history:
            self.url_completion_store.append([entry.get("url", ""), entry.get("title", "")])
        completion = Gtk.EntryCompletion()
        completion.set_model(self.url_completion_store)
        completion.set_text_column(0)
        completion.set_minimum_key_length(1)
        completion.set_popup_completion(True)
        completion.set_inline_completion(False)
        completion.set_match_func(self._url_completion_match, None)
        title_cell = Gtk.CellRendererText()
        completion.pack_start(title_cell, False)
        completion.add_attribute(title_cell, "text", 1)
        title_cell.set_property("scale", 0.85)
        completion.connect("match-selected", self._on_completion_match_selected)
        self.url_entry.set_completion(completion)

        # New Tab Button
        self.btn_new_tab = Gtk.Button.new_from_icon_name("tab-new-symbolic", Gtk.IconSize.BUTTON)
        self.btn_new_tab.get_style_context().add_class("flat-icon-btn")
        self.btn_new_tab.set_tooltip_text("New Tab (Ctrl+T)")
        self.btn_new_tab.connect("clicked", lambda b: self.create_new_tab(self.homepage))
        top_bar.pack_start(self.btn_new_tab, False, False, 0)

        # Action Buttons (grouped)
        action_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        action_group.get_style_context().add_class("nav-group")

        self.btn_screenshot = Gtk.Button.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.BUTTON)
        self.btn_screenshot.get_style_context().add_class("flat-icon-btn")
        self.btn_screenshot.set_tooltip_text("Take Webpage Screenshot")
        self.btn_screenshot.connect("clicked", self.on_screenshot_clicked)
        action_group.pack_start(self.btn_screenshot, False, False, 0)

        self.btn_dark = Gtk.Button.new_from_icon_name("weather-clear-night-symbolic", Gtk.IconSize.BUTTON)
        self.btn_dark.get_style_context().add_class("flat-icon-btn")
        self.btn_dark.set_tooltip_text("Toggle DarkReader Engine")
        self.btn_dark.connect("clicked", self.on_dark_clicked)
        action_group.pack_start(self.btn_dark, False, False, 0)

        self.btn_downloads = Gtk.Button.new_from_icon_name("folder-download-symbolic", Gtk.IconSize.BUTTON)
        self.btn_downloads.get_style_context().add_class("flat-icon-btn")
        self.btn_downloads.set_tooltip_text("Downloads Manager")
        self.btn_downloads.connect("clicked", self.on_downloads_clicked)
        action_group.pack_start(self.btn_downloads, False, False, 0)

        top_bar.pack_start(action_group, False, False, 0)

        self.btn_shield = Gtk.Button(label="🛡  0")
        self.btn_shield.get_style_context().add_class("btn-shield")
        self.btn_shield.set_tooltip_text("2-Stage Ad & Anti-Fingerprint Shield Active")
        top_bar.pack_start(self.btn_shield, False, False, 0)

        self.btn_settings = Gtk.Button.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        self.btn_settings.get_style_context().add_class("flat-icon-btn")
        self.btn_settings.set_tooltip_text("Menu & Settings ☰")
        self.btn_settings.connect("clicked", self.on_settings_clicked)
        top_bar.pack_start(self.btn_settings, False, False, 0)

        self.content_filter = None
        self._compile_content_blocker_filter()

        # UserScript objects are immutable once built (same as UserContentFilter
        # above), so build each one exactly once and hand the same instance to
        # every tab's UserContentManager instead of re-parsing/re-allocating 3
        # fresh WebKit2.UserScript objects on every single new tab.
        self.media_script = WebKit2.UserScript(
            MEDIA_POLYFILL_JS,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.START,
            None, None
        )
        self.farbling_script = WebKit2.UserScript(
            FARBLING_JS,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.START,
            None, None
        )
        self.prefetch_script = WebKit2.UserScript(
            PREFETCH_USER_SCRIPT,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.END,
            None, None
        )

        # Gtk.Notebook for Multi-Tab Architecture
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
        self.notebook.set_show_tabs(False)
        # Hide the tab strip entirely with a single tab (nothing to switch
        # between yet) and show it again as soon as there's a second one.
        self.notebook.connect("page-added", self._update_tabs_visibility)
        self.notebook.connect("page-removed", self._update_tabs_visibility)
        self.notebook.connect("switch-page", self.on_tab_changed)
        main_vbox.pack_start(self.notebook, True, True, 0)

        # Status Bar Footer
        self.statusbar = Gtk.Statusbar()
        self.context_id = self.statusbar.get_context_id("status")
        self.push_notification_status(f"Bharat Browser v{self.current_version} Ready | Made in INDIA 🇮🇳")
        main_vbox.pack_start(self.statusbar, False, False, 0)

        # Update Dialog Box Overlay
        self.update_dialog_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.update_dialog_box.get_style_context().add_class("update-dialog-box")
        self.update_dialog_box.set_halign(Gtk.Align.END)
        self.update_dialog_box.set_valign(Gtk.Align.END)
        self.update_dialog_box.set_margin_end(24)
        self.update_dialog_box.set_margin_bottom(36)

        icon_lbl = Gtk.Label(label="✔")
        icon_lbl.get_style_context().add_class("update-icon-text")
        self.update_dialog_label = Gtk.Label(label=f"Bharat Browser is working on latest version (v{self.current_version})")
        self.update_dialog_label.get_style_context().add_class("update-dialog-text")

        self.btn_restart_update = Gtk.Button(label="Restart Now")
        self.btn_restart_update.get_style_context().add_class("update-restart-btn")
        self.btn_restart_update.set_tooltip_text("Restart to apply the downloaded update")
        self.btn_restart_update.connect("clicked", lambda b: self.restart_application())
        self.btn_restart_update.set_no_show_all(True)
        self.btn_restart_update.hide()

        btn_close_update = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        btn_close_update.set_tooltip_text("Close Notification")
        btn_close_update.get_style_context().add_class("update-close-btn")
        btn_close_update.connect("clicked", lambda b: self.update_dialog_box.hide())

        self.update_dialog_box.pack_start(icon_lbl, False, False, 0)
        self.update_dialog_box.pack_start(self.update_dialog_label, False, False, 0)
        self.update_dialog_box.pack_start(self.btn_restart_update, False, False, 0)
        self.update_dialog_box.pack_start(btn_close_update, False, False, 0)

        self.overlay.add_overlay(self.update_dialog_box)
        self.update_dialog_box.hide()

        # Zoom Level Indicator Overlay — shown briefly on Ctrl+/Ctrl-/Ctrl+0
        # and Ctrl+scroll wheel, then auto-hidden.
        self.zoom_indicator = Gtk.Label()
        self.zoom_indicator.get_style_context().add_class("zoom-indicator")
        self.zoom_indicator.set_halign(Gtk.Align.CENTER)
        self.zoom_indicator.set_valign(Gtk.Align.START)
        self.zoom_indicator.set_margin_top(16)
        self.zoom_indicator.set_no_show_all(True)
        self.zoom_indicator.hide()
        self.overlay.add_overlay(self.zoom_indicator)
        self._zoom_indicator_hide_source = None

        # First-Run Greeting — shown once ever (never in private windows),
        # auto-hidden after 2 seconds.
        self.greeting_banner = Gtk.Label()
        self.greeting_banner.get_style_context().add_class("greeting-banner")
        self.greeting_banner.set_halign(Gtk.Align.CENTER)
        self.greeting_banner.set_valign(Gtk.Align.START)
        self.greeting_banner.set_margin_top(16)
        self.greeting_banner.set_no_show_all(True)
        self.greeting_banner.hide()
        self.overlay.add_overlay(self.greeting_banner)
        if not self.first_run_greeted and not self.is_private:
            self.first_run_greeted = True
            self.save_settings()
            GLib.idle_add(self.show_first_run_greeting)

        # Find-in-Page Bar Overlay — Ctrl+F to open, Escape to close.
        self.find_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.find_bar.get_style_context().add_class("find-bar")
        self.find_bar.set_halign(Gtk.Align.END)
        self.find_bar.set_valign(Gtk.Align.START)
        self.find_bar.set_margin_end(16)
        self.find_bar.set_margin_top(8)

        self.find_entry = Gtk.SearchEntry()
        self.find_entry.set_placeholder_text("Find in page...")
        self.find_entry.set_width_chars(24)
        self.find_entry.connect("search-changed", self.on_find_text_changed)
        self.find_entry.connect("activate", lambda e: self.find_next())
        self.find_entry.connect("key-press-event", self.on_find_entry_key_press)
        self.find_bar.pack_start(self.find_entry, False, False, 0)

        self.find_match_label = Gtk.Label(label="")
        self.find_match_label.get_style_context().add_class("find-match-label")
        self.find_bar.pack_start(self.find_match_label, False, False, 0)

        btn_find_prev = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.BUTTON)
        btn_find_prev.set_tooltip_text("Previous match (Shift+Enter)")
        btn_find_prev.connect("clicked", lambda b: self.find_previous())
        self.find_bar.pack_start(btn_find_prev, False, False, 0)

        btn_find_next = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.BUTTON)
        btn_find_next.set_tooltip_text("Next match (Enter)")
        btn_find_next.connect("clicked", lambda b: self.find_next())
        self.find_bar.pack_start(btn_find_next, False, False, 0)

        btn_find_close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        btn_find_close.set_tooltip_text("Close (Esc)")
        btn_find_close.connect("clicked", lambda b: self.close_find_bar())
        self.find_bar.pack_start(btn_find_close, False, False, 0)

        # Show children once now, then set no_show_all so the later top-level
        # app.show_all() in main() doesn't flash this open at every launch;
        # opening it later uses plain show() (not show_all()), which reveals
        # the container without needing to re-show already-visible children.
        self.find_bar.show_all()
        self.find_bar.set_no_show_all(True)
        self.find_bar.hide()
        self.overlay.add_overlay(self.find_bar)

        # Restore Session or Open Initial Tab (private windows never read or
        # write session.json, so no private URL ever touches disk)
        if self.is_private:
            initial_urls = [self.homepage]
        elif self.open_homepage_on_startup:
            initial_urls = [self.homepage]
        else:
            saved_session = load_session_state()
            initial_urls = saved_session.get("urls", [])
            if isinstance(initial_urls, str):
                initial_urls = [initial_urls]
            if not initial_urls:
                initial_urls = [self.homepage]

        for url in initial_urls:
            self.create_new_tab(url)

        # Keybindings (Ctrl+T, Ctrl+W, Ctrl+R)
        self.connect("key-press-event", self.on_key_press)
        self.connect("destroy", self.on_window_destroy)

        # Trigger Git Update Check after 30 sec (only the main window checks;
        # private windows shouldn't trip network activity or restart the app)
        if not self.is_private:
            GLib.timeout_add_seconds(30, self.start_auto_git_update_check)

        # React to system memory pressure by trimming WebKit's caches, instead
        # of only ever growing them for the lifetime of the process.
        try:
            self._memory_monitor = Gio.MemoryMonitor.dup_default()
            self._memory_monitor.connect("low-memory-warning", self.on_low_memory_warning)
        except Exception as e:
            print("Memory monitor note:", e)

    def on_low_memory_warning(self, monitor, level):
        print(f"Low-memory warning (level={level}), trimming caches.")
        try:
            self.context.clear_cache()
        except Exception as e:
            print("Cache trim note:", e)

    def _compile_content_blocker_filter(self):
        try:
            store_dir = os.path.join(CACHE_DIR, "content-filters")
            os.makedirs(store_dir, exist_ok=True)
            store = WebKit2.UserContentFilterStore.new(store_dir)
            rules_bytes = build_content_blocker_rules_json()
            store.save("bharat-adblock-v1", GLib.Bytes.new(rules_bytes), None, self._on_content_filter_saved, None)
        except Exception as e:
            print("Content filter compile note:", e)

    def _on_content_filter_saved(self, store, result, user_data):
        try:
            content_filter = store.save_finish(result)
        except Exception as e:
            print("Content filter save note:", e)
            return
        self.content_filter = content_filter
        # Apply retroactively to any tabs opened before compilation finished
        for i in range(self.notebook.get_n_pages()):
            tb = self.notebook.get_nth_page(i)
            if hasattr(tb, '_bharat_webview'):
                tb._bharat_webview.get_user_content_manager().add_filter(content_filter)
        print("Native ad/tracker content-blocker compiled and active.")

    def apply_custom_css(self):
        css_provider = Gtk.CssProvider()
        css_data = b"""
        * {
            font-family: "Dubai", -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", "Cantarell", "Ubuntu", sans-serif;
        }
        window { background-color: #0b0e14; }

        headerbar.bharat-titlebar {
            background-color: #0b0e14;
            background-image: none;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            box-shadow: none;
            padding: 0 4px;
            min-height: 0;
            color: #f8fafc;
        }
        /* Explicit, not just inherited: the system GTK theme (e.g. Adwaita)
        gives headerbar .title/.subtitle their own color that can win over
        plain inheritance, which is what left dialog title text unreadably
        dark against our dark background. */
        headerbar.bharat-titlebar .title,
        headerbar.bharat-titlebar .subtitle,
        headerbar.bharat-titlebar label {
            color: #f8fafc;
        }
        headerbar.bharat-titlebar button {
            min-height: 0;
            min-width: 0;
            padding: 2px;
            color: #f8fafc;
        }

        .top-bar {
            background-color: #11151d;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding: 0px 0;
        }

        .brand-label {
            font-weight: 700;
            color: #e2e8f0;
            font-size: 12px;
            letter-spacing: -0.1px;
        }

        /* Segmented "pill" grouping for nav & action buttons */
        .nav-group {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 999px;
            padding: 2px;
        }
        .flat-icon-btn {
            background: transparent;
            color: #9aa4b2;
            border: none;
            box-shadow: none;
            border-radius: 999px;
            padding: 4px 9px;
            min-width: 0;
            min-height: 0;
            transition: background 120ms ease, color 120ms ease;
        }
        .flat-icon-btn:hover {
            background: rgba(255, 255, 255, 0.08);
            color: #f1f5f9;
        }
        .flat-icon-btn:active {
            background: rgba(99, 102, 241, 0.25);
        }

        notebook { background-color: #0b0e14; border: none; }
        notebook header {
            background-color: #0b0e14;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding: 1px 8px 0 8px;
            min-height: 0;
        }
        notebook tab {
            background-color: transparent;
            color: #7c8798;
            border: none;
            border-radius: 10px 10px 0 0;
            padding: 2px 12px;
            margin-right: 2px;
            min-height: 0;
            transition: background 120ms ease, color 120ms ease;
        }
        notebook tab:hover {
            background-color: rgba(255, 255, 255, 0.04);
            color: #cbd5e1;
        }
        notebook tab:checked {
            background-color: #1a1f2b;
            color: #f8fafc;
            box-shadow: inset 0 -2px 0 0 #6366f1;
        }

        entry.url-entry {
            background-color: rgba(255, 255, 255, 0.05);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 999px;
            padding: 6px 14px;
            font-size: 13px;
            caret-color: #818cf8;
        }
        entry.url-entry image { color: #6b7686; margin-right: 2px; }
        entry.url-entry:focus {
            background-color: rgba(255, 255, 255, 0.07);
            border-color: #6366f1;
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.18);
        }
        entry.url-entry selection { background-color: #6366f1; color: #ffffff; }

        button {
            background: transparent;
            color: #cbd5e1;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 999px;
            padding: 5px 12px;
            font-size: 12px;
            font-weight: 600;
            transition: background 120ms ease, border-color 120ms ease;
        }
        button:hover {
            background: rgba(255, 255, 255, 0.06);
            border-color: rgba(255, 255, 255, 0.16);
            color: #ffffff;
        }

        .btn-shield {
            background: rgba(52, 211, 153, 0.10);
            color: #34d399;
            border: 1px solid rgba(52, 211, 153, 0.28);
            font-weight: 700;
            font-size: 11px;
            padding: 5px 12px;
        }
        .btn-shield:hover {
            background: rgba(52, 211, 153, 0.18);
            border-color: rgba(52, 211, 153, 0.4);
            color: #6ee7b7;
        }

        statusbar {
            background-color: #0b0e14;
            color: #5b6472;
            font-size: 10px;
            font-weight: 500;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding: 0px 12px;
            min-height: 0;
        }

        .update-dialog-box {
            background: #151a24;
            color: #f8fafc;
            border: 1px solid rgba(52, 211, 153, 0.35);
            border-radius: 14px;
            padding: 12px 20px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.55);
        }
        .update-icon-text { font-weight: 900; color: #10b981; font-size: 15px; }
        .update-restart-btn {
            background: rgba(99, 102, 241, 0.18);
            color: #c7d2fe;
            border: 1px solid rgba(99, 102, 241, 0.4);
            border-radius: 999px;
            padding: 4px 12px;
            font-size: 12px;
            font-weight: 700;
        }
        .update-restart-btn:hover {
            background: rgba(99, 102, 241, 0.3);
            color: #ffffff;
        }
        .update-dialog-text { font-weight: 600; color: #f8fafc; font-size: 13px; }
        .update-close-btn {
            background: transparent;
            border: none;
            color: #7c8798;
            border-radius: 999px;
            padding: 2px;
        }
        .update-close-btn:hover { background: rgba(255, 255, 255, 0.08); color: #ffffff; }

        .zoom-indicator {
            background: rgba(17, 21, 29, 0.92);
            color: #f8fafc;
            font-weight: 700;
            font-size: 16px;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 10px;
            padding: 8px 18px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5);
        }

        .greeting-banner {
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.25), rgba(16, 185, 129, 0.18));
            color: #f8fafc;
            font-weight: 700;
            font-size: 16px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 10px;
            padding: 10px 22px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5);
        }

        .find-bar {
            background: rgba(17, 21, 29, 0.96);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 10px;
            padding: 6px 8px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5);
        }
        .find-bar entry {
            background-color: rgba(255, 255, 255, 0.06);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.18);
        }
        .find-match-label {
            color: #94a3b8;
            font-size: 90%;
        }

        /* Settings / Downloads / message dialogs: match the dark app chrome
        instead of falling back to the light system GTK theme, which they
        do by default since they're plain Gtk.Dialog/Gtk.MessageDialog
        windows outside the styled main window. */
        window.bharat-dialog, .bharat-dialog {
            background-color: #11151d;
            color: #f1f5f9;
        }
        .bharat-dialog label { color: #f1f5f9; }
        .bharat-dialog checkbutton, .bharat-dialog radiobutton { color: #f1f5f9; }
        .bharat-dialog check, .bharat-dialog radio {
            background-color: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #f1f5f9;
        }
        .bharat-dialog check:checked, .bharat-dialog radio:checked {
            background-color: #6366f1;
            border-color: #6366f1;
        }
        .bharat-dialog .settings-section-frame {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
        }
        .bharat-dialog .settings-section-title {
            color: #93c5fd;
            padding: 0 4px;
        }
        .bharat-dialog .settings-hint-label {
            color: #94a3b8;
            font-size: 90%;
        }
        .bharat-dialog entry {
            background-color: rgba(255, 255, 255, 0.06);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.18);
        }
        """
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def apply_dark_titlebar(self, dialog, title_text):
        """Give a Gtk.Dialog the same dark Gtk.HeaderBar the main window uses.
        Without this, GTK/the window manager draws the dialog's title bar
        using the system GTK theme (usually light), which the app's own CSS
        provider can't reach since that chrome isn't part of the dialog's
        content widget tree."""
        titlebar = Gtk.HeaderBar()
        titlebar.set_show_close_button(True)
        titlebar.set_title(title_text)
        titlebar.get_style_context().add_class("bharat-titlebar")
        dialog.set_titlebar(titlebar)

    # Multi-Tab Architecture Helper Methods
    def create_new_tab(self, url=None, webview=None):
        tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # webview is only pre-supplied for popups/target="_blank" links via
        # on_create_webview(), which must hand WebKit a related WebView
        # rather than an independent one for the navigation to go through.
        load_initial_uri = webview is None
        if webview is None:
            webview = WebKit2.WebView.new_with_context(self.context)
        webview.set_settings(self.web_settings)

        # Inject UserScripts
        ucm = webview.get_user_content_manager()

        if self.content_filter is not None:
            ucm.add_filter(self.content_filter)

        # 1. Media Codec Polyfill, 1b. Anti-Fingerprinting Farbling Engine,
        # 2. Smart Link Prefetching — shared, pre-built instances (see __init__)
        ucm.add_script(self.media_script)
        ucm.add_script(self.farbling_script)
        ucm.add_script(self.prefetch_script)

        # Signals
        webview.connect("load-changed", self.on_load_changed)
        webview.connect("resource-load-started", self.on_resource_load_started)
        webview.connect("web-process-terminated", self.on_web_process_terminated)
        webview.connect("permission-request", self.on_permission_request)
        webview.connect("load-failed-with-tls-errors", self.on_load_failed_with_tls_errors)
        webview.connect("load-failed", self.on_load_failed)
        # Without this, WebKit silently drops any navigation that wants a new
        # window/tab (target="_blank", window.open(), middle-click, OAuth
        # popups, etc.) instead of doing anything visible.
        webview.connect("create", self.on_create_webview)

        # Ctrl+scroll to zoom. A Gtk.EventControllerScroll attached directly
        # to the webview (tried in a prior version, both CAPTURE and BUBBLE
        # phase) fully claims scroll input at the GTK controller-framework
        # level, which turned out to be mutually exclusive with WebKit's own
        # native page-scroll handling — it broke normal mouse-wheel/touchpad
        # scrolling entirely. The plain "scroll-event" signal doesn't have
        # that problem: returning False lets the event continue on to
        # WebKit's normal handling exactly like any unhandled GTK signal.
        webview.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        webview.connect("scroll-event", self.on_webview_scroll)

        find_controller = webview.get_find_controller()
        find_controller.connect("found-text", self.on_find_found_text, webview)
        find_controller.connect("failed-to-find-text", self.on_find_failed_text, webview)

        tab_box.pack_start(webview, True, True, 0)
        tab_box.show_all()

        # Custom Tab Header Widget (Label + Close Button)
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab_label = Gtk.Label(label="New Tab")
        tab_label.set_max_width_chars(18)
        tab_label.set_ellipsize(3) # PANGO_ELLIPSIZE_END
        
        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_tooltip_text("Close Tab")
        close_btn.connect("clicked", lambda b: self.close_tab(tab_box))

        header_box.pack_start(tab_label, True, True, 0)
        header_box.pack_start(close_btn, False, False, 0)
        header_box.show_all()

        tab_box._bharat_webview = webview
        tab_box._bharat_label = tab_label

        page_num = self.notebook.append_page(tab_box, header_box)
        self.notebook.set_tab_reorderable(tab_box, True)
        self.notebook.set_current_page(page_num)

        if load_initial_uri:
            webview.load_uri(url or self.homepage)
        return webview

    def on_create_webview(self, webview, navigation_action):
        related_webview = WebKit2.WebView.new_with_related_view(webview)
        self.create_new_tab(webview=related_webview)
        return related_webview

    def on_window_destroy(self, window):
        for i in range(self.notebook.get_n_pages()):
            tab_box = self.notebook.get_nth_page(i)
            if hasattr(tab_box, '_bharat_webview'):
                self._flush_page_view(tab_box._bharat_webview)

    def close_tab(self, tab_box):
        if hasattr(tab_box, '_bharat_webview'):
            self._crash_counts.pop(id(tab_box._bharat_webview), None)
            self._load_failure_counts.pop(id(tab_box._bharat_webview), None)
            self._flush_page_view(tab_box._bharat_webview)
        page_num = self.notebook.page_num(tab_box)
        if page_num != -1:
            self.notebook.remove_page(page_num)
        if self.notebook.get_n_pages() == 0:
            self.create_new_tab(self.homepage)

    def get_active_webview(self):
        page_num = self.notebook.get_current_page()
        if page_num != -1:
            tab_box = self.notebook.get_nth_page(page_num)
            if hasattr(tab_box, '_bharat_webview'):
                return tab_box._bharat_webview
        return None

    def get_active_tab_box(self):
        page_num = self.notebook.get_current_page()
        if page_num != -1:
            return self.notebook.get_nth_page(page_num)
        return None

    def _update_tabs_visibility(self, notebook, child=None, page_num=None):
        notebook.set_show_tabs(notebook.get_n_pages() > 1)

    def on_tab_changed(self, notebook, page, page_num):
        if self.find_bar.get_visible():
            self.close_find_bar()
        webview = self.get_active_webview()
        if webview:
            uri = webview.get_uri() or ""
            title = webview.get_title() or f"Bharat Browser v{self.current_version}"
            self.url_entry.set_text(uri)
            self.update_security_icon(uri)
            self.set_title(f"{title} - Bharat Browser v{self.current_version}")
            self.statusbar.push(self.context_id, f"Ready | {uri}")

    def update_security_icon(self, uri):
        if uri.startswith("https://"):
            self.url_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "channel-secure-symbolic")
            self.url_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY, "Secure connection (HTTPS)")
        elif uri.startswith("http://"):
            self.url_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "channel-insecure-symbolic")
            self.url_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY, "Not secure (HTTP)")
        else:
            self.url_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find-symbolic")
            self.url_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY, "")

    MAX_AUTO_RELOAD_CRASHES = 3

    def on_web_process_terminated(self, webview, reason):
        key = id(webview)
        count = self._crash_counts.get(key, 0) + 1
        self._crash_counts[key] = count
        print(f"Web process terminated (reason={reason}), crash #{count} for this tab")

        if count > self.MAX_AUTO_RELOAD_CRASHES:
            self.statusbar.push(self.context_id, "⚠️ This tab crashed repeatedly and was not reloaded automatically.")
            error_html = (
                "<html><body style='background:#0b0e14;color:#f8fafc;"
                "font-family:sans-serif;padding:40px;'>"
                "<h2>This page keeps crashing</h2>"
                "<p>Bharat Browser stopped auto-reloading it after repeated crashes. "
                "Use the Reload button to try again manually.</p>"
                "</body></html>"
            )
            GLib.idle_add(lambda: webview.load_html(error_html, None))
            return

        uri = webview.get_uri() or self.homepage
        GLib.idle_add(lambda: webview.load_uri(uri))
        self.statusbar.push(self.context_id, "⚠️ Web process recovered automatically.")

    def on_load_failed_with_tls_errors(self, webview, failing_uri, certificate, errors):
        # No "proceed anyway" bypass: this is a privacy/security-first
        # browser, and offering to click through an invalid cert on a
        # connection that HTTPS-enforcement just upgraded (or that was
        # https:// to begin with) would defeat that guarantee for exactly
        # the connections where it matters most (public hostnames, not
        # local-network devices, which HTTPS enforcement already exempts).
        print(f"TLS certificate error for {failing_uri} (errors={errors}); load blocked.")
        host = (urllib.parse.urlparse(failing_uri).hostname or failing_uri)
        error_html = (
            "<html><body style='background:#0b0e14;color:#f8fafc;"
            "font-family:sans-serif;padding:40px;'>"
            "<h2>⚠️ Your connection is not private</h2>"
            f"<p>Bharat Browser blocked this page because <b>{GLib.markup_escape_text(host)}</b> "
            "presented an invalid or untrusted security certificate.</p>"
            "<p>This browser does not offer a way to bypass certificate errors, "
            "to keep HTTPS connections trustworthy.</p>"
            "</body></html>"
        )
        GLib.idle_add(lambda: webview.load_html(error_html, failing_uri))
        self.statusbar.push(self.context_id, f"⚠️ Blocked invalid certificate on {host}")
        return True

    MAX_AUTO_RETRY_LOAD_FAILURES = 1
    LOAD_RETRY_DELAY_MS = 600

    def on_load_failed(self, webview, load_event, failing_uri, error):
        # Fires for any network-level load failure (connection reset during
        # TLS handshake, DNS hiccups, timeouts, etc.) — separate from
        # on_load_failed_with_tls_errors, which is only for certificate
        # *validation* problems. CANCELLED just means the user navigated
        # away or stopped the load; that's normal, not a real failure.
        if error.matches(WebKit2.network_error_quark(), WebKit2.NetworkError.CANCELLED):
            return True

        key = id(webview)
        count = self._load_failure_counts.get(key, 0) + 1
        self._load_failure_counts[key] = count
        print(f"Load failed for {failing_uri} (attempt {count}): {error.message}")

        if count <= self.MAX_AUTO_RETRY_LOAD_FAILURES:
            # Many of these (e.g. "Connection reset by peer" mid-TLS-handshake)
            # are transient and succeed on a plain retry, so retry once
            # silently before showing the user an error page.
            self.statusbar.push(self.context_id, f"⚠️ Load failed, retrying... ({error.message})")
            GLib.timeout_add(self.LOAD_RETRY_DELAY_MS, lambda: (webview.load_uri(failing_uri), False)[1])
            return True

        self._load_failure_counts.pop(key, None)
        host = urllib.parse.urlparse(failing_uri).hostname or failing_uri
        error_html = (
            "<html><body style='background:#0b0e14;color:#f8fafc;"
            "font-family:sans-serif;padding:40px;'>"
            "<h2>⚠️ This page didn't load</h2>"
            f"<p>Bharat Browser couldn't reach <b>{GLib.markup_escape_text(host)}</b>:</p>"
            f"<p style='color:#94a3b8'>{GLib.markup_escape_text(error.message)}</p>"
            "<p>Use the Reload button to try again.</p>"
            "</body></html>"
        )
        GLib.idle_add(lambda: webview.load_html(error_html, failing_uri))
        self.statusbar.push(self.context_id, f"⚠️ Failed to load {host}")
        return True

    def on_key_press(self, widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        shift = event.state & Gdk.ModifierType.SHIFT_MASK
        if ctrl:
            keyval = event.keyval
            if shift and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self.open_private_window()
                return True
            elif keyval in (Gdk.KEY_t, Gdk.KEY_T):
                self.create_new_tab(self.homepage)
                return True
            elif keyval in (Gdk.KEY_w, Gdk.KEY_W):
                active_box = self.get_active_tab_box()
                if active_box:
                    self.close_tab(active_box)
                return True
            elif keyval in (Gdk.KEY_r, Gdk.KEY_R):
                webview = self.get_active_webview()
                if webview:
                    webview.reload()
                return True
            elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.adjust_zoom(0.1)
                return True
            elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.adjust_zoom(-0.1)
                return True
            elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self.adjust_zoom(reset=True)
                return True
            elif keyval in (Gdk.KEY_f, Gdk.KEY_F):
                self.open_find_bar()
                return True
            elif keyval in (Gdk.KEY_h, Gdk.KEY_H):
                self.open_history_tab()
                return True
        elif event.keyval == Gdk.KEY_Escape and self.find_bar.get_visible():
            self.close_find_bar()
            return True
        return False

    ZOOM_MIN = 0.3
    ZOOM_MAX = 3.0

    ZOOM_INDICATOR_AUTOHIDE_SECONDS = 2

    def adjust_zoom(self, delta=0.0, reset=False):
        webview = self.get_active_webview()
        if not webview:
            return
        new_level = 1.0 if reset else webview.get_zoom_level() + delta
        new_level = max(self.ZOOM_MIN, min(self.ZOOM_MAX, new_level))
        webview.set_zoom_level(new_level)
        self.show_zoom_indicator(new_level)

    def show_zoom_indicator(self, zoom_level):
        """Floating badge with the current zoom %, shown on every zoom change
        (Ctrl+/Ctrl-/Ctrl+0 and Ctrl+scroll) and auto-hidden after 2 seconds."""
        self.zoom_indicator.set_text(f"🔍 {round(zoom_level * 100)}%")
        self.zoom_indicator.show()
        # Cancel any previously scheduled auto-hide so a fast run of zoom
        # events doesn't stack timers that later hide the badge out from
        # under a still-current reading.
        if self._zoom_indicator_hide_source is not None:
            GLib.source_remove(self._zoom_indicator_hide_source)

        def _hide():
            self.zoom_indicator.hide()
            self._zoom_indicator_hide_source = None
            return False
        self._zoom_indicator_hide_source = GLib.timeout_add_seconds(self.ZOOM_INDICATOR_AUTOHIDE_SECONDS, _hide)

    GREETING_AUTOHIDE_SECONDS = 2

    def show_first_run_greeting(self):
        try:
            username = getpass.getuser()
        except Exception:
            username = ""
        text = f"नमस्ते {username} 👋" if username else "नमस्ते 👋"
        self.greeting_banner.set_text(text)
        self.greeting_banner.show()
        GLib.timeout_add_seconds(self.GREETING_AUTOHIDE_SECONDS, lambda: (self.greeting_banner.hide(), False)[1])
        return False

    def on_webview_scroll(self, webview, event):
        if not (event.state & Gdk.ModifierType.CONTROL_MASK):
            return False  # let the page scroll normally
        if event.direction == Gdk.ScrollDirection.UP:
            self.adjust_zoom(0.1)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self.adjust_zoom(-0.1)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _, delta_y = event.get_scroll_deltas()
            if delta_y < 0:
                self.adjust_zoom(0.1)
            elif delta_y > 0:
                self.adjust_zoom(-0.1)
        return True  # consume the event: don't also scroll/pinch-zoom the page

    FIND_OPTIONS = WebKit2.FindOptions.CASE_INSENSITIVE | WebKit2.FindOptions.WRAP_AROUND
    FIND_MAX_MATCH_COUNT = 1000

    def open_find_bar(self):
        self.find_bar.show()
        self.find_entry.grab_focus()
        text = self.find_entry.get_text()
        if text:
            self.find_entry.select_region(0, -1)
            self._run_find(text)

    def close_find_bar(self):
        self.find_bar.hide()
        self.find_match_label.set_text("")
        webview = self.get_active_webview()
        if webview:
            webview.get_find_controller().search_finish()
        webview_focus = self.get_active_webview()
        if webview_focus:
            webview_focus.grab_focus()

    def _run_find(self, text):
        webview = self.get_active_webview()
        if not webview:
            return
        if not text:
            webview.get_find_controller().search_finish()
            self.find_match_label.set_text("")
            return
        webview.get_find_controller().search(text, self.FIND_OPTIONS, self.FIND_MAX_MATCH_COUNT)

    def on_find_text_changed(self, entry):
        self._run_find(entry.get_text())

    def on_find_entry_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_find_bar()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (event.state & Gdk.ModifierType.SHIFT_MASK):
            self.find_previous()
            return True
        return False

    def find_next(self):
        webview = self.get_active_webview()
        if webview and self.find_entry.get_text():
            webview.get_find_controller().search_next()

    def find_previous(self):
        webview = self.get_active_webview()
        if webview and self.find_entry.get_text():
            webview.get_find_controller().search_previous()

    def on_find_found_text(self, controller, match_count, webview):
        if self.get_active_webview() == webview:
            self.find_match_label.set_text(f"{match_count} match" + ("es" if match_count != 1 else ""))

    def on_find_failed_text(self, controller, webview):
        if self.get_active_webview() == webview:
            self.find_match_label.set_text("No matches")

    def open_private_window(self):
        win = BharatBrowserWindow(private=True)
        self._private_windows.append(win)
        win.connect("destroy", lambda w: self._private_windows.remove(win) if win in self._private_windows else None)
        win.show_all()
        return win

    # Download Manager Handlers
    def get_downloads_dir(self):
        xdg_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        downloads_dir = xdg_dir or os.path.expanduser("~/Downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        return downloads_dir

    def unique_download_path(self, downloads_dir, filename):
        target_path = os.path.join(downloads_dir, filename)
        if not os.path.exists(target_path):
            return target_path
        base, ext = os.path.splitext(filename)
        counter = 1
        while True:
            candidate = os.path.join(downloads_dir, f"{base} ({counter}){ext}")
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def on_download_started(self, context, download):
        downloads_dir = self.get_downloads_dir()

        filename = "downloaded_file"
        try:
            req = download.get_request()
            if req and req.get_uri():
                filename = os.path.basename(urllib.parse.urlparse(req.get_uri()).path) or "downloaded_file"
        except Exception as e:
            print("Download filename resolution note:", e)

        target_path = self.unique_download_path(downloads_dir, filename)
        filename = os.path.basename(target_path)
        download.set_destination("file://" + target_path)

        entry = {"filename": filename, "path": target_path, "status": "Downloading..."}
        self.downloads_history.append(entry)
        self.statusbar.push(self.context_id, f"📥 Download Started: {filename} -> {downloads_dir}")

        download.connect("finished", lambda d: self.on_download_finished(entry))
        download.connect("failed", lambda d, err: self.on_download_failed(entry, err))

    def on_download_finished(self, entry):
        entry["status"] = "Completed ✅"
        self.statusbar.push(self.context_id, f"✅ Download Completed: {entry['filename']}")

    def on_download_failed(self, entry, error):
        entry["status"] = "Failed ❌"
        self.statusbar.push(self.context_id, f"❌ Download Failed: {entry['filename']} ({error})")

    def on_downloads_clicked(self, btn):
        dialog = Gtk.Dialog(
            title="📥 Downloads Manager",
            transient_for=self,
            modal=True,
            destroy_with_parent=True
        )
        dialog.get_style_context().add_class("bharat-dialog")
        self.apply_dark_titlebar(dialog, "📥 Downloads Manager")
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(460, 320)

        content_area = dialog.get_content_area()
        content_area.set_margin_start(16)
        content_area.set_margin_end(16)
        content_area.set_margin_top(16)
        content_area.set_margin_bottom(16)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_area.add(vbox)

        lbl_header = Gtk.Label(label="📥 Recent Downloads")
        lbl_header.get_style_context().add_class("brand-label")
        vbox.pack_start(lbl_header, False, False, 0)

        if not self.downloads_history:
            lbl_empty = Gtk.Label(label="No downloads in this session yet.")
            vbox.pack_start(lbl_empty, False, False, 12)
        else:
            for item in self.downloads_history[-8:]:
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name_lbl = Gtk.Label(label=f"📄 {item['filename']} [{item['status']}]")
                hbox.pack_start(name_lbl, True, True, 0)
                vbox.pack_start(hbox, False, False, 2)

        btn_open_folder = Gtk.Button(label="📁 Open Downloads Folder")
        btn_open_folder.connect("clicked", lambda b: subprocess.Popen(["xdg-open", self.get_downloads_dir()]))
        vbox.pack_start(btn_open_folder, False, False, 8)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def start_auto_git_update_check(self):
        threading.Thread(target=self.async_git_update_check, daemon=True).start()
        return False

    @staticmethod
    def _version_parts(v):
        # Stop at the first non-numeric token (pre-release suffixes like
        # "-rc1"): a naive `[^0-9.]` strip previously merged "1.2.10-rc1"
        # into "1.2.101", corrupting the comparison instead of ignoring the
        # suffix.
        parts = []
        for token in re.split(r'[.\-+]', v):
            m = re.match(r'\d+', token)
            if not m:
                break
            parts.append(int(m.group()))
        return parts

    def compare_versions(self, v1, v2):
        p1, p2 = self._version_parts(v1), self._version_parts(v2)
        for a, b in zip(p1, p2):
            if a > b: return 1
            if a < b: return -1
        return (len(p1) > len(p2)) - (len(p1) < len(p2))

    def async_git_update_check(self):
        try:
            url = "https://raw.githubusercontent.com/Sangam1112/bharat-browser/master/package.json"
            req = urllib.request.Request(url, headers={"User-Agent": f"BharatBrowser/{self.current_version}"})
            with urllib.request.urlopen(req, timeout=6) as response:
                if response.status != 200:
                    GLib.idle_add(self.show_latest_version_notification)
                    return
                data = json.loads(response.read(1 << 20).decode('utf-8'))
                remote_version = data.get("version", "").strip()
                remote_sha256 = data.get("sha256", "").strip().lower()

            if not remote_version or self.compare_versions(remote_version, self.current_version) <= 0:
                print(f"Bharat Browser is up to date (v{self.current_version}).")
                GLib.idle_add(self.show_latest_version_notification)
                return

            print(f"Update available: v{self.current_version} -> v{remote_version}. Downloading...")
            installed = self.download_and_install_update(remote_version, remote_sha256)
            if installed:
                print(f"Update v{remote_version} downloaded and installed; restart to apply.")
            else:
                print(f"Update v{remote_version} available but not auto-installed.")
            GLib.idle_add(self.show_update_notification_dialog, remote_version, installed)
        except Exception as e:
            print("Git update check note:", e)
            GLib.idle_add(self.show_latest_version_notification)

    MAX_UPDATE_SOURCE_BYTES = 5 * 1024 * 1024  # sanity cap; the script is ~60KB today

    def download_and_install_update(self, remote_version, remote_sha256=""):
        """Download bharat_browser.py for the announced release tag and replace the
        running script in place. Only runs if the target file is writable by this
        user; otherwise the update is left for the system package manager / manual
        copy. Fetches from an immutable tag (not the mutable 'master' branch) and,
        when package.json publishes a "sha256" field for the release, verifies the
        downloaded bytes against it before installing anything."""
        target_path = os.path.abspath(__file__)
        if not os.access(target_path, os.W_OK):
            print(f"Update available but {target_path} is not writable; skipping auto-install.")
            return False
        try:
            src_url = f"https://raw.githubusercontent.com/Sangam1112/bharat-browser/v{remote_version}/bharat_browser.py"
            req = urllib.request.Request(src_url, headers={"User-Agent": f"BharatBrowser/{self.current_version}"})
            with urllib.request.urlopen(req, timeout=10) as response:
                new_source = response.read(self.MAX_UPDATE_SOURCE_BYTES + 1)
            if len(new_source) > self.MAX_UPDATE_SOURCE_BYTES:
                print("Auto-update install failed: release payload exceeded the expected size, aborting.")
                return False

            if remote_sha256:
                digest = hashlib.sha256(new_source).hexdigest()
                if digest != remote_sha256:
                    print(f"Auto-update install failed: checksum mismatch (expected {remote_sha256}, got {digest}).")
                    return False
            else:
                print("Auto-update note: release did not publish a sha256 checksum; installing on tag pin + syntax check only.")

            # Reject anything that isn't at least syntactically valid Python
            ast.parse(new_source.decode('utf-8'))

            tmp_path = target_path + ".update-tmp"
            with open(tmp_path, "wb") as f:
                f.write(new_source)
            os.chmod(tmp_path, 0o755)
            os.replace(tmp_path, target_path)
            print(f"Installed update to {target_path}.")
            return True
        except Exception as e:
            print("Auto-update install failed:", e)
            return False

    def restart_application(self):
        try:
            script = os.path.abspath(__file__)
            os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])
        except Exception as e:
            print("Restart failed:", e)

    STATUSBAR_AUTOHIDE_SECONDS = 5

    def push_notification_status(self, message):
        self.statusbar.show_all()
        self.statusbar.push(self.context_id, message)
        # Cancel any previously scheduled auto-hide so a fast run of status
        # updates doesn't stack multiple timers that later hide the bar out
        # from under a newer message still meant to be showing.
        pending = getattr(self, "_statusbar_hide_source", None)
        if pending is not None:
            GLib.source_remove(pending)
        def _hide():
            self.statusbar.hide()
            self._statusbar_hide_source = None
            return False
        self._statusbar_hide_source = GLib.timeout_add_seconds(self.STATUSBAR_AUTOHIDE_SECONDS, _hide)

    VERSION_NOTIFICATION_AUTOHIDE_SECONDS = 2

    def show_latest_version_notification(self):
        self.update_dialog_label.set_text(f"Browser is working on latest version (v{self.current_version})")
        self.update_dialog_box.show_all()
        self.btn_restart_update.hide()
        self.push_notification_status(f"✅ Browser is working on latest version (v{self.current_version})")
        GLib.timeout_add_seconds(self.VERSION_NOTIFICATION_AUTOHIDE_SECONDS, lambda: (self.update_dialog_box.hide(), False)[1])

    def show_update_notification_dialog(self, version_str, installed=True):
        if installed:
            self.update_dialog_label.set_text(f"Downloaded update v{version_str} — click Restart Now to apply")
            self.update_dialog_box.show_all()
            self.btn_restart_update.show()
            self.push_notification_status(f"🎉 Downloaded update v{version_str}. Restart to apply.")
        else:
            self.update_dialog_label.set_text(f"Update v{version_str} available — install via your package manager")
            self.update_dialog_box.show_all()
            self.btn_restart_update.hide()
            self.push_notification_status(f"⬆️ Update v{version_str} available (auto-install needs write access)")
            GLib.timeout_add_seconds(8, lambda: (self.update_dialog_box.hide(), False)[1])

    def on_resource_load_started(self, webview, resource, request):
        uri = request.get_uri()
        if not uri:
            return

        if self.https_enabled and uri.startswith("http://"):
            host = (urllib.parse.urlparse(uri).hostname or '').lower()
            if not is_local_network_host(host):
                new_uri = uri.replace("http://", "https://", 1)
                request.set_uri(new_uri)
                uri = new_uri

        if self.clearurls_enabled:
            sanitized = sanitize_url(uri)
            if sanitized != uri:
                request.set_uri(sanitized)
                uri = sanitized

        if self.adblock_enabled and is_ad_or_tracker(uri):
            self.blocked_count += 1
            GLib.idle_add(self.update_shield_badge)
            if ".js" in uri or "script" in uri:
                request.set_uri("data:application/javascript,")
            elif ".json" in uri:
                request.set_uri("data:application/json,{}")
            else:
                request.set_uri("data:text/plain,")

    def update_shield_badge(self):
        self.btn_shield.set_label(f"🛡️ {self.blocked_count}")

    def on_url_activate(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        if not text.startswith("http://") and not text.startswith("https://") and not text.startswith("about:"):
            host_candidate = text.split('/', 1)[0].split(':', 1)[0]
            looks_like_url = " " not in text and ("." in host_candidate or is_local_network_host(host_candidate))
            if looks_like_url:
                # Bare local hostnames (routers, printers, dev boxes) rarely
                # have a real HTTPS cert to upgrade to; real FQDNs still go
                # through the on_resource_load_started HTTPS-upgrade path.
                scheme = "http://" if is_local_network_host(host_candidate) else "https://"
                text = scheme + text
            else:
                template = SEARCH_ENGINES.get(self.search_engine, SEARCH_ENGINES[DEFAULT_SEARCH_ENGINE])
                text = template.format(query=urllib.parse.quote(text))

        webview = self.get_active_webview()
        if webview:
            webview.load_uri(text)

    def _url_completion_match(self, completion, key, tree_iter, data):
        model = completion.get_model()
        url = (model[tree_iter][0] or "").lower()
        title = (model[tree_iter][1] or "").lower()
        return key in url or key in title

    def _on_completion_match_selected(self, completion, model, tree_iter):
        url = model[tree_iter][0]
        self.url_entry.set_text(url)
        self.url_entry.set_position(-1)
        self.on_url_activate(self.url_entry)
        return True

    def record_history_entry(self, url, title):
        """Add/refresh a URL-bar autocomplete + history-dashboard entry.
        Guards is_private itself (not just at the on_load_changed call site)
        so no future call path can accidentally write private-window
        browsing to disk. Preserves accumulated time-on-page and visit
        count across re-visits instead of resetting them."""
        if self.is_private:
            return
        if not url or url.startswith("about:"):
            return
        existing = next((e for e in self.url_history if e.get("url") == url), None)
        total_seconds = existing.get("total_seconds", 0.0) if existing else 0.0
        visits = existing.get("visits", 0) + 1 if existing else 1
        self.url_history = [e for e in self.url_history if e.get("url") != url]
        self.url_history.insert(0, {
            "url": url,
            "title": title or url,
            "total_seconds": total_seconds,
            "visits": visits,
            "last_visited": time.time(),
        })
        del self.url_history[HISTORY_MAX_ENTRIES:]
        save_url_history(self.url_history)

        self.url_completion_store.clear()
        for entry in self.url_history:
            self.url_completion_store.append([entry.get("url", ""), entry.get("title", "")])

    def _start_page_view(self, webview, url):
        if self.is_private or not url or url.startswith("about:"):
            return
        self._page_view_start[id(webview)] = (url, time.monotonic())

    def _flush_page_view(self, webview):
        """Add elapsed time on the page this webview was previously showing
        to that URL's running total. Called when the tab navigates to a new
        page, closes, or the window closes, so time-on-page is captured
        without needing to track focus/visibility precisely."""
        entry = self._page_view_start.pop(id(webview), None)
        if not entry or self.is_private:
            return
        url, start = entry
        elapsed = time.monotonic() - start
        if elapsed < 0.5:
            return  # ignore instant navigations (redirects, typos, etc.)
        for hist_entry in self.url_history:
            if hist_entry.get("url") == url:
                hist_entry["total_seconds"] = hist_entry.get("total_seconds", 0.0) + elapsed
                save_url_history(self.url_history)
                return

    @staticmethod
    def _format_duration(seconds):
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"

    def build_history_dashboard_html(self):
        rows_html = ""
        entries = sorted(self.url_history, key=lambda e: e.get("total_seconds", 0.0), reverse=True)
        total_seconds_all = sum(e.get("total_seconds", 0.0) for e in entries)
        total_visits_all = sum(e.get("visits", 0) for e in entries)

        for entry in entries:
            url = entry.get("url", "")
            title = entry.get("title") or url
            domain = urllib.parse.urlparse(url).hostname or url
            duration = self._format_duration(entry.get("total_seconds", 0.0))
            visits = entry.get("visits", 0)
            last_visited = entry.get("last_visited")
            last_visited_str = (
                GLib.DateTime.new_from_unix_local(int(last_visited)).format("%d %b %Y, %H:%M")
                if last_visited else "—"
            )
            rows_html += f"""
            <tr onclick="window.location.href='{GLib.markup_escape_text(url)}'">
                <td class="title-cell">
                    <div class="title">{GLib.markup_escape_text(title)}</div>
                    <div class="domain">{GLib.markup_escape_text(domain)}</div>
                </td>
                <td class="num-cell">{duration}</td>
                <td class="num-cell">{visits}</td>
                <td class="num-cell muted">{last_visited_str}</td>
            </tr>"""

        if not entries:
            rows_html = """
            <tr><td colspan="4" class="empty-state">No browsing history yet.</td></tr>"""

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>History — Bharat Browser</title>
<style>
    body {{ background: #0b0e14; color: #f8fafc; font-family: -apple-system, 'Segoe UI', sans-serif;
            margin: 0; padding: 32px 40px; }}
    h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
    .subtitle {{ color: #94a3b8; font-size: 13px; margin-bottom: 24px; }}
    .stats {{ display: flex; gap: 16px; margin-bottom: 28px; }}
    .stat-card {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
                  border-radius: 10px; padding: 14px 20px; }}
    .stat-value {{ font-size: 20px; font-weight: 700; color: #93c5fd; }}
    .stat-label {{ font-size: 12px; color: #94a3b8; }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{ text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
                color: #64748b; padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,0.12); }}
    tbody tr {{ cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.06); }}
    tbody tr:hover {{ background: rgba(99,102,241,0.1); }}
    td {{ padding: 12px; vertical-align: middle; }}
    .title-cell .title {{ font-weight: 600; font-size: 14px; }}
    .title-cell .domain {{ font-size: 12px; color: #64748b; margin-top: 2px; }}
    .num-cell {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .num-cell.muted {{ color: #94a3b8; font-size: 13px; }}
    .empty-state {{ text-align: center; color: #64748b; padding: 60px 0; }}
</style></head>
<body>
    <h1>📊 Browsing History</h1>
    <div class="subtitle">Sites ranked by time spent, most-visited-in-time first.</div>
    <div class="stats">
        <div class="stat-card"><div class="stat-value">{len(entries)}</div><div class="stat-label">Sites tracked</div></div>
        <div class="stat-card"><div class="stat-value">{self._format_duration(total_seconds_all)}</div><div class="stat-label">Total time tracked</div></div>
        <div class="stat-card"><div class="stat-value">{total_visits_all}</div><div class="stat-label">Total visits</div></div>
    </div>
    <table>
        <thead><tr><th>Site</th><th style="text-align:right">Time Spent</th><th style="text-align:right">Visits</th><th style="text-align:right">Last Visited</th></tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
</body></html>"""

    def open_history_tab(self):
        webview = self.create_new_tab(url="about:blank")
        html = self.build_history_dashboard_html()
        # A fake base URI here (e.g. "about:history") gets treated as a real
        # navigation target and fails to load, which — now that load-failed
        # triggers our own retry/error-page handling — replaced this page
        # with the "didn't load" error page instead of the dashboard. None
        # is what the existing crash-error page uses for the same reason.
        GLib.idle_add(lambda: webview.load_html(html, None))

    def on_back_clicked(self, btn):
        webview = self.get_active_webview()
        if webview and webview.can_go_back():
            webview.go_back()

    def on_forward_clicked(self, btn):
        webview = self.get_active_webview()
        if webview and webview.can_go_forward():
            webview.go_forward()

    def on_reload_clicked(self, btn):
        webview = self.get_active_webview()
        if webview:
            webview.reload()

    def on_load_changed(self, webview, load_event):
        if load_event == WebKit2.LoadEvent.STARTED:
            self.statusbar.push(self.context_id, "Loading webpage...")
        elif load_event == WebKit2.LoadEvent.FINISHED:
            self._crash_counts.pop(id(webview), None)
            uri = webview.get_uri() or ""
            title = webview.get_title() or "New Tab"
            # WebKit fires load-changed(FINISHED) even for a load that just
            # failed (load-failed fires first, with the webview's URI back
            # to empty at this point) — only clear the retry counter on a
            # genuine success, or on_load_failed's retry-once logic would
            # get silently reset every time and retry forever instead of
            # ever reaching the "show a friendly error page" threshold.
            if uri:
                self._load_failure_counts.pop(id(webview), None)
            
            active_wv = self.get_active_webview()
            if active_wv == webview:
                self.url_entry.set_text(uri)
                self.update_security_icon(uri)
                self.set_title(f"{title} - Bharat Browser v{self.current_version}")
                self.statusbar.push(self.context_id, f"Ready | {uri}")

            # Update tab label
            for i in range(self.notebook.get_n_pages()):
                tab_box = self.notebook.get_nth_page(i)
                if hasattr(tab_box, '_bharat_webview') and tab_box._bharat_webview == webview:
                    if hasattr(tab_box, '_bharat_label'):
                        tab_box._bharat_label.set_text(title)
                    break

            # Save session state across tabs (skipped for private windows)
            if not self.is_private:
                urls = []
                for i in range(self.notebook.get_n_pages()):
                    tb = self.notebook.get_nth_page(i)
                    if hasattr(tb, '_bharat_webview'):
                        u = tb._bharat_webview.get_uri()
                        if u and not u.startswith("about:"):
                            urls.append(u)
                if urls:
                    save_session_state(urls)

                self._flush_page_view(webview)
                self.record_history_entry(uri, title)
                self._start_page_view(webview, uri)

            # Injections
            if self.dark_mode_active:
                self.apply_dark_reader_to_webview(webview)

    def save_settings(self):
        save_persistent_settings({
            "dark_mode": self.dark_mode_active,
            "adblock_enabled": self.adblock_enabled,
            "clearurls_enabled": self.clearurls_enabled,
            "https_enabled": self.https_enabled,
            "dev_tools_enabled": self.dev_tools_enabled,
            "webrtc_enabled": self.webrtc_enabled,
            "search_engine": self.search_engine,
            "homepage": self.homepage,
            "open_homepage_on_startup": self.open_homepage_on_startup,
            "first_run_greeted": self.first_run_greeted
        })

    def on_dark_clicked(self, btn):
        self.dark_mode_active = not self.dark_mode_active
        self.save_settings()
        for i in range(self.notebook.get_n_pages()):
            tb = self.notebook.get_nth_page(i)
            if hasattr(tb, '_bharat_webview'):
                wv = tb._bharat_webview
                if self.dark_mode_active:
                    self.apply_dark_reader_to_webview(wv)
                else:
                    self.remove_dark_reader_from_webview(wv)
        if self.dark_mode_active:
            self.statusbar.push(self.context_id, "DarkReader Engine Enabled 🌙")
        else:
            self.statusbar.push(self.context_id, "DarkReader Engine Disabled ☀️")

    def execute_js_on_webview(self, webview, js_code):
        try:
            if hasattr(webview, "evaluate_javascript"):
                webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            else:
                webview.run_javascript(js_code, None, None, None)
        except Exception:
            try:
                webview.run_javascript(js_code, None, None, None)
            except Exception as e:
                print("JS execution note:", e)

    def apply_dark_reader_to_webview(self, webview):
        js = f"""
        (function() {{
            var id = 'bharat-darkreader-style';
            var existing = document.getElementById(id);
            if (!existing) {{
                var style = document.createElement('style');
                style.id = id;
                style.innerHTML = `{DARKREADER_CSS}`;
                document.head.appendChild(style);
            }}
        }})();
        """
        self.execute_js_on_webview(webview, js)

    def remove_dark_reader_from_webview(self, webview):
        js = """
        (function() {
            var el = document.getElementById('bharat-darkreader-style');
            if (el) el.remove();
        })();
        """
        self.execute_js_on_webview(webview, js)

    def on_screenshot_clicked(self, btn):
        webview = self.get_active_webview()
        if not webview:
            return
        self.statusbar.push(self.context_id, "📸 Capturing webpage screenshot...")
        webview.get_snapshot(
            WebKit2.SnapshotRegion.FULL_DOCUMENT,
            WebKit2.SnapshotOptions.NONE,
            None,
            self.on_snapshot_ready,
            None
        )

    def on_snapshot_ready(self, webview, result, user_data):
        try:
            surface = webview.get_snapshot_finish(result)
            width, height = surface.get_width(), surface.get_height()
            # Flatten onto an opaque RGB24 surface first: the snapshot surface
            # carries an alpha channel, and some gdk-pixbuf JPEG backends
            # (e.g. glycin on newer Fedora/GNOME) refuse to encode RGBA as JPEG.
            opaque_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
            ctx = cairo.Context(opaque_surface)
            ctx.set_source_rgb(1, 1, 1)
            ctx.paint()
            ctx.set_source_surface(surface, 0, 0)
            ctx.paint()
            pixbuf = Gdk.pixbuf_get_from_surface(opaque_surface, 0, 0, width, height)
            desktop_dir = os.path.expanduser("~/Desktop")
            os.makedirs(desktop_dir, exist_ok=True)
            timestamp = GLib.DateTime.new_now_local().format("%Y%m%d_%H%M%S")
            filepath = self.unique_download_path(desktop_dir, f"BharatScreenshot_{timestamp}.jpeg")
            filename = os.path.basename(filepath)
            pixbuf.savev(filepath, "jpeg", ["quality"], ["90"])
            self.statusbar.push(self.context_id, f"📸 Screenshot saved to Desktop: {filename}")

            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                destroy_with_parent=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Screenshot Saved to Desktop"
            )
            dialog.get_style_context().add_class("bharat-dialog")
            dialog.format_secondary_text(f"File: {filename}\nSaved in ~/Desktop")
            dialog.run()
            dialog.destroy()
        except Exception as e:
            self.statusbar.push(self.context_id, f"❌ Screenshot failed: {str(e)}")

    @staticmethod
    def _settings_section(title_text):
        """A titled, visually boxed group for related settings, GNOME-Settings
        style, instead of a bare bold label — makes it obvious at a glance
        where one group of options ends and the next begins."""
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        frame.get_style_context().add_class("settings-section-frame")

        title_lbl = Gtk.Label(xalign=0.0)
        title_lbl.set_markup(f"<b>{GLib.markup_escape_text(title_text)}</b>")
        title_lbl.get_style_context().add_class("settings-section-title")
        frame.set_label_widget(title_lbl)
        frame.set_label_align(0.03, 0.5)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        inner.set_margin_top(10)
        inner.set_margin_bottom(12)
        frame.add(inner)
        return frame, inner

    def on_settings_clicked(self, btn):
        title_text = f"Browser Settings & Extensions (v{self.current_version})"
        dialog = Gtk.Dialog(
            title=title_text,
            transient_for=self,
            modal=True,
            destroy_with_parent=True
        )
        dialog.get_style_context().add_class("bharat-dialog")
        self.apply_dark_titlebar(dialog, title_text)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_response(Gtk.ResponseType.CLOSE)
        # Tall enough that most setups see everything without scrolling, but
        # still resizable (GTK dialogs are resizable by default) for anyone
        # who wants more room, and the scroller below still kicks in on
        # small/cramped screens instead of clipping content unreachably.
        dialog.set_default_size(540, 680)

        content_area = dialog.get_content_area()
        content_area.set_margin_start(0)
        content_area.set_margin_end(0)
        content_area.set_margin_top(0)
        content_area.set_margin_bottom(0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # content_area.add(scroller) would pack it without expand/fill here,
        # so it only ever gets its tiny default minimum height instead of
        # the dialog's available space — which is what made the scroll area
        # effectively unusable. pack_start with expand=True, fill=True fixes it.
        content_area.pack_start(scroller, True, True, 0)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        vbox.set_margin_start(18)
        vbox.set_margin_end(18)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        scroller.add(vbox)

        title_lbl = Gtk.Label(label=f"⚙️ Bharat Browser Settings (v{self.current_version})")
        title_lbl.get_style_context().add_class("brand-label")
        vbox.pack_start(title_lbl, False, False, 0)

        # --- General ---------------------------------------------------
        general_frame, general_box = self._settings_section("General")
        vbox.pack_start(general_frame, False, False, 0)

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_lbl = Gtk.Label(label="🔍 Search engine")
        search_lbl.set_size_request(150, -1)
        search_lbl.set_xalign(0.0)
        search_box.pack_start(search_lbl, False, False, 0)
        search_combo = Gtk.ComboBoxText()
        search_combo.set_tooltip_text("Used when you type a search term (not a web address) into the address bar.")
        for engine_name in SEARCH_ENGINES:
            search_combo.append_text(engine_name)
        search_combo.set_active(list(SEARCH_ENGINES.keys()).index(self.search_engine))
        search_combo.connect("changed", lambda cb: (setattr(self, 'search_engine', cb.get_active_text()), self.save_settings()))
        search_box.pack_start(search_combo, True, True, 0)
        general_box.pack_start(search_box, False, False, 0)

        general_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        home_lbl = Gtk.Label(label="🏠 Homepage", xalign=0.0)
        home_lbl.set_tooltip_text("Opened by new tabs (Ctrl+T), the New Tab button, and when the last tab closes.")
        general_box.pack_start(home_lbl, False, False, 0)

        home_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        home_entry = Gtk.Entry()
        home_entry.set_text(self.homepage)
        home_entry.set_placeholder_text("e.g. example.com or https://example.com")
        home_entry.set_hexpand(True)
        home_box.pack_start(home_entry, True, True, 0)

        btn_set_home = Gtk.Button(label="Set")
        btn_set_home.set_tooltip_text("Save this address as your homepage")
        home_box.pack_start(btn_set_home, False, False, 0)

        btn_reset_home = Gtk.Button(label="Use Google")
        btn_reset_home.set_tooltip_text(f"Reset homepage to {DEFAULT_HOMEPAGE}")
        home_box.pack_start(btn_reset_home, False, False, 0)
        general_box.pack_start(home_box, False, False, 0)

        home_status = Gtk.Label(xalign=0.0)
        home_status.get_style_context().add_class("settings-hint-label")
        general_box.pack_start(home_status, False, False, 0)

        def _apply_homepage(new_value):
            self.homepage = sanitize_homepage_url(new_value)
            home_entry.set_text(self.homepage)
            self.save_settings()
            home_status.set_text(f"✅ Homepage set to {self.homepage}")
            GLib.timeout_add(3000, lambda: (home_status.set_text(""), False)[1])

        btn_set_home.connect("clicked", lambda b: _apply_homepage(home_entry.get_text()))
        home_entry.connect("activate", lambda e: _apply_homepage(e.get_text()))
        btn_reset_home.connect("clicked", lambda b: _apply_homepage(DEFAULT_HOMEPAGE))

        chk_homepage_startup = Gtk.CheckButton(label="🚀 Open homepage on startup")
        chk_homepage_startup.set_tooltip_text(
            "On: every launch opens your homepage.\n"
            "Off (default): launch restores your previous tabs, same as before."
        )
        chk_homepage_startup.set_active(self.open_homepage_on_startup)
        chk_homepage_startup.connect(
            "toggled",
            lambda cb: (setattr(self, 'open_homepage_on_startup', cb.get_active()), self.save_settings())
        )
        general_box.pack_start(chk_homepage_startup, False, False, 0)

        # --- Privacy & Security -----------------------------------------
        privacy_frame, privacy_box = self._settings_section("Privacy & Security")
        vbox.pack_start(privacy_frame, False, False, 0)

        chk_dark = Gtk.CheckButton(label="🌙 DarkReader Engine (High-Contrast Webpages)")
        chk_dark.set_active(self.dark_mode_active)
        chk_dark.connect("toggled", lambda cb: self.on_dark_clicked(self.btn_dark))
        privacy_box.pack_start(chk_dark, False, False, 0)

        chk_adblock = Gtk.CheckButton(label="🛡️ Ad & Tracker Blocking")
        chk_adblock.set_tooltip_text("Blocks known ad/tracker domains using a built-in blocklist.")
        chk_adblock.set_active(self.adblock_enabled)
        chk_adblock.connect("toggled", lambda cb: (setattr(self, 'adblock_enabled', cb.get_active()), self.save_settings()))
        privacy_box.pack_start(chk_adblock, False, False, 0)

        chk_clearurls = Gtk.CheckButton(label="🔗 Strip Tracking Parameters from URLs")
        chk_clearurls.set_tooltip_text("Removes utm_*, fbclid, gclid, and similar tracking parameters before navigating.")
        chk_clearurls.set_active(self.clearurls_enabled)
        chk_clearurls.connect("toggled", lambda cb: (setattr(self, 'clearurls_enabled', cb.get_active()), self.save_settings()))
        privacy_box.pack_start(chk_clearurls, False, False, 0)

        chk_https = Gtk.CheckButton(label="🔒 HTTPS Enforcement (Auto-Upgrade HTTP)")
        chk_https.set_active(self.https_enabled)
        chk_https.connect("toggled", lambda cb: (setattr(self, 'https_enabled', cb.get_active()), self.save_settings()))
        privacy_box.pack_start(chk_https, False, False, 0)

        chk_webrtc = Gtk.CheckButton(label="🎥 WebRTC / Camera & Mic")
        chk_webrtc.set_tooltip_text("Off by default — WebRTC can leak your local network IP address even behind a VPN.")
        chk_webrtc.set_active(self.webrtc_enabled)
        chk_webrtc.connect("toggled", lambda cb: self.on_webrtc_toggled(cb.get_active()))
        privacy_box.pack_start(chk_webrtc, False, False, 0)

        # --- Advanced ----------------------------------------------------
        advanced_frame, advanced_box = self._settings_section("Advanced")
        vbox.pack_start(advanced_frame, False, False, 0)

        chk_devtools = Gtk.CheckButton(label="🛠️ Developer Tools (Web Inspector)")
        chk_devtools.set_active(self.dev_tools_enabled)
        chk_devtools.connect("toggled", lambda cb: self.on_devtools_toggled(cb.get_active()))
        advanced_box.pack_start(chk_devtools, False, False, 0)

        # --- Actions -------------------------------------------------------
        actions_frame, actions_box = self._settings_section("Actions")
        vbox.pack_start(actions_frame, False, False, 0)

        btn_history = Gtk.Button(label="📊 Show History")
        btn_history.set_tooltip_text("Ctrl+H")
        btn_history.connect("clicked", lambda b: (self.open_history_tab(), dialog.destroy()))
        actions_box.pack_start(btn_history, False, False, 0)

        btn_clear = Gtk.Button(label="🗑️ Clear Browsing History & Cookies")
        btn_clear.connect("clicked", self.on_clear_cache_clicked)
        actions_box.pack_start(btn_clear, False, False, 0)

        btn_private = Gtk.Button(label="🕵 New Private Window (Ctrl+Shift+N)")
        btn_private.connect("clicked", lambda b: (self.open_private_window(), dialog.destroy()))
        actions_box.pack_start(btn_private, False, False, 0)

        about_lbl = Gtk.Label(label=f"Bharat Browser v{self.current_version} | Engineered in INDIA 🇮🇳")
        about_lbl.get_style_context().add_class("settings-hint-label")
        vbox.pack_start(about_lbl, False, False, 4)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def on_devtools_toggled(self, active):
        self.dev_tools_enabled = active
        self.web_settings.set_enable_developer_extras(active)
        self.save_settings()

    def on_webrtc_toggled(self, active):
        self.webrtc_enabled = active
        self.web_settings.set_enable_webrtc(active)
        self.web_settings.set_enable_media_stream(active)
        self.save_settings()

    def on_permission_request(self, webview, request):
        uri = webview.get_uri() or "This site"
        kind_labels = {
            WebKit2.UserMediaPermissionRequest: "camera/microphone access",
            WebKit2.GeolocationPermissionRequest: "your location",
            WebKit2.NotificationPermissionRequest: "notifications",
        }
        kind = "a permission"
        for cls, label in kind_labels.items():
            if isinstance(request, cls):
                kind = label
                break

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Allow {kind}?"
        )
        dialog.get_style_context().add_class("bharat-dialog")
        dialog.format_secondary_text(uri)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            request.allow()
        else:
            request.deny()
        return True

    def on_clear_cache_clicked(self, btn):
        try:
            if hasattr(self.context, "get_website_data_manager"):
                wdm = self.context.get_website_data_manager()
                wdm.clear(WebKit2.WebsiteDataTypes.ALL, 0, None, None, None)
            else:
                cm = self.context.get_cookie_manager()
                cm.delete_all_cookies()
        except Exception:
            try:
                cm = self.context.get_cookie_manager()
                cm.delete_all_cookies()
            except Exception:
                pass

        self.context.clear_cache()

        self.url_history = []
        save_url_history(self.url_history)
        self.url_completion_store.clear()
        self._page_view_start.clear()

        self.statusbar.push(self.context_id, "🧹 Browsing History & Cache Cleared!")

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Browsing History & Cookies Cleared"
        )
        dialog.get_style_context().add_class("bharat-dialog")
        dialog.format_secondary_text("All browsing history, cached web data, and tracking cookies have been successfully cleared.")
        dialog.run()
        dialog.destroy()

def main():
    app = BharatBrowserWindow()
    app.connect("destroy", Gtk.main_quit)
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
