#!/usr/bin/env python3
"""
Bharat Browser v1.3.1 - GTK3 / WebKit2 Python Application
Modern, Ultra-Fast, Multi-Tab, and Privacy-First Web Browser engineered for Linux (Ubuntu)
"""
import sys
import os
import json

# CONFIG_DIR/CONFIG_FILE are also used later (load_persistent_settings() and
# friends) — defined here first because Low Memory Mode's single-process
# request has to be decided before WebKit2 is imported below: unlike Low
# Memory Mode's other effect (WebKitWebContext's cache model, set further
# down), WEBKIT_USE_SINGLE_WEB_PROCESS is only read by WebKit at process
# startup, not something that can be toggled on an already-running session.
# Kept set here despite that: confirmed empirically that WebKitGTK 2.54
# does NOT honor it (4 open tabs still spawned 4 separate WebProcess
# instances with this set to "1"), so it isn't advertised as a working
# effect in the Settings UI — left in place only in case it still works on
# other WebKit versions.
CONFIG_DIR = os.path.expanduser("~/.config/bharat-browser")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")


def _read_low_memory_mode_setting():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("low_memory_mode", False))
    except Exception:
        return False


# Enable GPU Hardware Acceleration & System-Level Acceleration Flags
os.environ["WEBKIT_FORCE_COMPOSITING_MODE"] = "1"
os.environ["GST_VAAPI_ALL_DRIVERS"] = "1"
os.environ["GST_DEBUG"] = "0"
os.environ["WEBKIT_USE_SINGLE_WEB_PROCESS"] = "1" if _read_low_memory_mode_setting() else "0"

import ast
import getpass
import hashlib
import ipaddress
import re
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

from gi.repository import Gtk, Gdk, WebKit2, GLib, Gio, Pango

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

# Tracks how many top-level BharatBrowserWindow instances (the main window
# plus any private windows, which are siblings, not children, of it) are
# still open, so Gtk.main_quit() only fires once the last one closes instead
# of whenever the main window happens to close first and silently tearing
# down every open private window mid-session with no warning.
_LIVE_WINDOW_COUNT = 0

CACHE_DIR = os.path.expanduser("~/.cache/bharat-browser")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")
HISTORY_FILE = os.path.join(CONFIG_DIR, "history.json")
HISTORY_MAX_ENTRIES = 500
BOOKMARKS_FILE = os.path.join(CONFIG_DIR, "bookmarks.json")
BOOKMARKS_MAX_ENTRIES = 5000

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

def _write_json_private(path, data, indent=2):
    """Write JSON with 0600 permissions from creation, not applied after the
    fact, so browsing history/session data is never briefly world/group
    readable and isn't left exposed if a chmod step were skipped."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)

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
        _write_json_private(temp_file, {"urls": urls, "timestamp": time.time()}, indent=None)
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
        _write_json_private(temp_file, entries, indent=None)
        os.replace(temp_file, HISTORY_FILE)
    except Exception as e:
        print("History save note:", e)

def load_bookmarks():
    """Returns a list of {"url":..., "title":..., "added": epoch} dicts in the
    order they were added. Never called for private windows, matching the
    history/session pattern."""
    try:
        if os.path.exists(BOOKMARKS_FILE):
            with open(BOOKMARKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [b for b in data if isinstance(b, dict) and isinstance(b.get("url"), str)]
    except Exception as e:
        print("Bookmarks load note:", e)
    return []

def save_bookmarks(entries):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temp_file = BOOKMARKS_FILE + ".tmp"
        _write_json_private(temp_file, entries)
        os.replace(temp_file, BOOKMARKS_FILE)
    except Exception as e:
        print("Bookmarks save note:", e)

_GPU_INFO_CACHE = None

def detect_gpu_info():
    """Best-effort GPU identification for the Settings 'GPU Acceleration' card.
    Tries glxinfo first since it reports the actual OpenGL renderer WebKit's
    compositor will use (and whether it's really hardware-accelerated),
    falling back to lspci's PCI device name. Never raises — display text only.
    Cached at module scope: the GPU doesn't change mid-session, so every new
    window (including private windows) reuses the first result instead of
    re-running subprocess calls — each with its own multi-second timeout —
    on every single window open."""
    global _GPU_INFO_CACHE
    if _GPU_INFO_CACHE is not None:
        return _GPU_INFO_CACHE
    _GPU_INFO_CACHE = _detect_gpu_info_uncached()
    return _GPU_INFO_CACHE

def _detect_gpu_info_uncached():
    try:
        out = subprocess.run(
            ["glxinfo", "-B"], capture_output=True, text=True, timeout=3
        ).stdout
        renderer = re.search(r"OpenGL renderer string:\s*(.+)", out)
        direct = re.search(r"direct rendering:\s*(.+)", out)
        if renderer:
            label = renderer.group(1).strip()
            if direct and not direct.group(1).strip().lower().startswith("yes"):
                label += " (no direct rendering — software fallback)"
            return label
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["lspci", "-nn"], capture_output=True, text=True, timeout=3
        ).stdout
        for line in out.splitlines():
            if re.search(r"VGA compatible controller|3D controller|Display controller", line):
                match = re.search(r":\s*(.+?)\s*\[[0-9a-f]{4}:[0-9a-f]{4}\](?:\s*\(rev.*\))?\s*$", line)
                if match:
                    return match.group(1).strip()
                return line.split(":", 2)[-1].strip()
    except Exception:
        pass
    return "Unknown GPU (detection unavailable)"

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

    // mouseover bubbles from every element the pointer crosses (including
    // deeply nested children of a link), so this can fire hundreds of times
    // per second while the mouse moves over a link-dense page. The previous
    // version ran document.querySelector() — a DOM scan that gets slower as
    // more prefetch <link> tags accumulate — on every single firing, even
    // when re-hovering the exact same link. Track already-seen origins in
    // memory instead (O(1), no DOM query at all on repeat hovers), and cap
    // how many distinct origins get a <link> added so a long session on a
    // link-heavy page can't grow <head> unboundedly.
    const seenOrigins = new Set();
    const MAX_PREFETCH_ORIGINS = 30;

    function prefetchLink(e) {
        try {
            const target = e.target.closest && e.target.closest('a[href^="http"]');
            if (!target) return;
            const origin = new URL(target.href).origin;
            if (seenOrigins.has(origin)) return;
            if (seenOrigins.size >= MAX_PREFETCH_ORIGINS) return;
            seenOrigins.add(origin);

            const dnsLink = document.createElement('link');
            dnsLink.rel = 'dns-prefetch';
            dnsLink.href = origin;
            document.head.appendChild(dnsLink);

            const connLink = document.createElement('link');
            connLink.rel = 'preconnect';
            connLink.href = origin;
            document.head.appendChild(connLink);
        } catch(err){}
    }

    document.addEventListener('mouseover', prefetchLink, { passive: true });
})();
"""

DARKREADER_CSS = """
html {
    filter: invert(90%) hue-rotate(180deg) !important;
    /* The invert filter applies to html's own background too, so this must
       be the pre-inversion colour: white inverts (90%) to a dark #191919,
       whereas #121212 would flip to light grey behind transparent pages. */
    background-color: #ffffff !important;
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

    function boostVideo(v) {
        if (!v.__bharat_boosted__) {
            v.__bharat_boosted__ = true;
            v.preload = 'auto';
        }
    }

    function optimizeVideoElements() {
        try {
            document.querySelectorAll('video').forEach(boostVideo);
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
    // new work to do anyway. Only inspect each mutation's addedNodes (and
    // their subtrees), not a full document.querySelectorAll('video') scan
    // on every single mutation batch: on a page that mutates independently
    // and often (live feeds, ad refreshes, chat widgets — confirmed via
    // testing that each separate update triggers its own callback, not one
    // shared batch), a full-document rescan on every one of those is a real,
    // measurable, and entirely avoidable CPU cost that scales with page size.
    function handleMutations(mutationsList) {
        try {
            for (const mutation of mutationsList) {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== 1) continue;
                    if (node.tagName === 'VIDEO') {
                        boostVideo(node);
                    } else if (node.querySelectorAll) {
                        node.querySelectorAll('video').forEach(boostVideo);
                    }
                }
            }
        } catch(e){}
    }

    try {
        const observer = new MutationObserver(handleMutations);
        observer.observe(document.documentElement || document, { childList: true, subtree: true });
    } catch(e){}
})();
"""

class BharatBrowserWindow(Gtk.Window):
    _global_css_loaded = False

    def __init__(self, private=False):
        self.current_version = "1.3.1"
        self.is_private = private
        title_suffix = " (Private)" if private else ""
        super().__init__(title=f"Bharat Browser v{self.current_version}{title_suffix}")
        self._private_windows = []
        global _LIVE_WINDOW_COUNT
        _LIVE_WINDOW_COUNT += 1
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
        # Background tab suspension: keyed by id(tab_box). _tab_last_active
        # records when each tab last STOPPED being the foreground tab (used
        # to decide what's "inactive long enough"); _suspended_session_states
        # holds the saved WebKitWebViewSessionState (back-forward history +
        # current page) for tabs whose content has been unloaded to free
        # memory; _suspended_tab_titles holds the label text to restore
        # (with a sleep-indicator prefix) so the generic title-update path
        # doesn't overwrite it with an "about:blank" fallback while suspended.
        self._tab_last_active = {}
        self._suspended_session_states = {}
        self._suspended_tab_titles = {}
        self._suspended_tab_uris = {}
        self._current_active_tab_box = None

        saved_settings = load_persistent_settings()
        self.dark_mode_active = saved_settings.get("dark_mode", False)
        self.download_dir = saved_settings.get("download_dir", "") or ""
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
        self.low_memory_mode = saved_settings.get("low_memory_mode", False)
        self.tab_suspension_enabled = saved_settings.get("tab_suspension_enabled", True)
        self.clear_history_on_exit = saved_settings.get("clear_history_on_exit", False)
        self.gpu_acceleration_enabled = saved_settings.get("gpu_acceleration_enabled", True)
        # glxinfo/lspci can take ~1s+ (3s timeout each) and the result is only
        # display text in Settings, so detect it off the GTK thread instead of
        # blocking the first window from appearing.
        self.gpu_info_label = _GPU_INFO_CACHE if _GPU_INFO_CACHE is not None else "Detecting..."
        if _GPU_INFO_CACHE is None:
            threading.Thread(target=self._detect_gpu_info_async, daemon=True).start()

        # URL-bar autocomplete history. Never loaded/written for private
        # windows, matching the session-state privacy guarantee.
        self._history_save_source = None
        self._dns_prefetched = set()
        self._dns_typing_source = None
        self._session_save_source = None
        self.url_history = [] if self.is_private else load_url_history()
        self.bookmarks = [] if self.is_private else load_bookmarks()

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
            # Low Memory Mode trades WEB_BROWSER's aggressive disk/memory
            # caching (optimized for fast repeat navigation) for
            # DOCUMENT_VIEWER's much smaller footprint. Unlike the
            # single-process setting below, WebKitWebContext's cache model
            # can be changed on an already-running context, so this part of
            # Low Memory Mode takes effect immediately, no restart needed.
            model = WebKit2.CacheModel.DOCUMENT_VIEWER if self.low_memory_mode else WebKit2.CacheModel.WEB_BROWSER
            self.context.set_cache_model(model)
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
        # GPU Acceleration toggle (Settings > Performance): when on, page
        # compositing/canvas/WebGL are rendered on the GPU instead of raster
        # buffers in system RAM, which is the actual memory saving here (not
        # the always-on WEBKIT_FORCE_COMPOSITING_MODE env var above, which is
        # left untouched since it's required just to get direct rendering at
        # all on this WebKit version). When off, everything falls back to
        # CPU/software rendering — useful on systems with broken/blacklisted
        # GPU drivers, at the cost of higher RAM/CPU use.
        self.web_settings.set_hardware_acceleration_policy(
            WebKit2.HardwareAccelerationPolicy.ALWAYS if self.gpu_acceleration_enabled
            else WebKit2.HardwareAccelerationPolicy.NEVER
        )
        self.web_settings.set_enable_webgl(self.gpu_acceleration_enabled)
        if hasattr(self.web_settings, 'set_enable_2d_canvas_acceleration'):
            self.web_settings.set_enable_2d_canvas_acceleration(self.gpu_acceleration_enabled)
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
        self.url_entry.connect("changed", self.on_url_entry_changed)
        self.url_entry.connect("icon-press", self.on_url_entry_icon_press)
        top_bar.pack_start(self.url_entry, True, True, 4)

        # Autocomplete from browsing history (url, title)
        self.url_completion_store = Gtk.ListStore(str, str)
        for entry in self.url_history:
            self.url_completion_store.append([entry.get("url", ""), entry.get("title", "")])
        completion = Gtk.EntryCompletion()
        completion.set_model(self.url_completion_store)
        completion.set_minimum_key_length(1)
        completion.set_popup_completion(True)
        completion.set_inline_completion(False)
        completion.set_match_func(self._url_completion_match, None)
        # The popup is a native window sized to its widest row, and GDK
        # can't create one wider than 32767px: a single very long history
        # URL (e.g. a 5000-char search URL) used to make GTK's Wayland
        # backend dereference a NULL cairo surface and segfault. Ellipsize
        # both columns instead of using set_text_column(), which installs an
        # unbounded renderer. The model still holds the full URL/title.
        url_cell = Gtk.CellRendererText()
        url_cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        url_cell.set_property("max-width-chars", 90)
        completion.pack_start(url_cell, True)
        completion.add_attribute(url_cell, "text", 0)
        title_cell = Gtk.CellRendererText()
        title_cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        title_cell.set_property("max-width-chars", 40)
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

        self.btn_bookmarks = Gtk.Button.new_from_icon_name("user-bookmarks-symbolic", Gtk.IconSize.BUTTON)
        self.btn_bookmarks.get_style_context().add_class("flat-icon-btn")
        self.btn_bookmarks.set_tooltip_text("Bookmark Manager (Ctrl+Shift+O)")
        self.btn_bookmarks.connect("clicked", lambda b: self.open_bookmark_manager())
        action_group.pack_start(self.btn_bookmarks, False, False, 0)

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
        # Dark mode is a WebKit UserStyleSheet, not a JS-injected <style>: it is
        # applied by the engine when each document is created (no white flash
        # before load-finished), survives pages that rewrite <head>/<html> and
        # SPA navigations, and follows every navigation in the tab until it is
        # removed. TOP_FRAME only: the filter is on <html>, so applying it inside
        # iframes as well would invert their content twice.
        self.dark_stylesheet = WebKit2.UserStyleSheet(
            DARKREADER_CSS,
            WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserStyleLevel.USER,
            None, None
        )
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

        # Periodic sweep to suspend background tabs that have been inactive
        # for a while, freeing most of their WebProcess memory without
        # closing the tab. Runs regardless of is_private — suspension is
        # purely in-memory (session state is kept in a Python dict, never
        # written to disk), so it doesn't weaken the private-window
        # no-trace-on-disk guarantee the way history/session saving would.
        GLib.timeout_add_seconds(self.TAB_SUSPENSION_CHECK_INTERVAL_SECONDS, self._check_tab_suspension)

        # React to system memory pressure by trimming WebKit's caches, instead
        # of only ever growing them for the lifetime of the process.
        try:
            self._memory_monitor = Gio.MemoryMonitor.dup_default()
            self._memory_monitor.connect("low-memory-warning", self.on_low_memory_warning)
        except Exception as e:
            print("Memory monitor note:", e)

    def _detect_gpu_info_async(self):
        self.gpu_info_label = detect_gpu_info()

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
        # Screen-wide CSS is process-global and identical for every window,
        # so only the first window (main or private) needs to load it —
        # otherwise each new window/private window added another duplicate
        # Gtk.CssProvider to the screen's provider list forever, with no way
        # to remove it, since GTK has no add_provider_for_screen dedup.
        if BharatBrowserWindow._global_css_loaded:
            return
        BharatBrowserWindow._global_css_loaded = True
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
            border-radius: 4px;
            min-width: 16px;
            min-height: 16px;
        }
        .bharat-dialog check:checked, .bharat-dialog radio:checked {
            background-color: #6366f1;
            border-color: #6366f1;
        }
        .bharat-dialog .settings-section-frame {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 10px;
        }
        .bharat-dialog .settings-card {
            background-color: rgba(255, 255, 255, 0.035);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            padding: 12px 14px;
        }
        .bharat-dialog .settings-row {
            padding: 4px 2px;
        }
        .bharat-dialog .settings-row-title {
            color: #f8fafc;
            font-size: 13px;
            font-weight: 600;
        }
        .bharat-dialog .settings-section-title {
            color: #93c5fd;
            font-weight: 700;
            font-size: 12px;
            padding: 0 4px;
            letter-spacing: 0.2px;
        }
        .bharat-dialog .settings-hint-label {
            color: #94a3b8;
            font-size: 11px;
        }
        .bharat-dialog entry {
            background-color: rgba(255, 255, 255, 0.06);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            padding: 5px 10px;
        }
        .bharat-dialog entry:focus {
            border-color: #6366f1;
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
        }
        .bharat-dialog combobox button {
            background-color: rgba(255, 255, 255, 0.06);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            padding: 4px 10px;
        }
        .settings-stack-switcher {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 999px;
            padding: 3px;
        }
        .settings-stack-switcher button {
            background: transparent;
            color: #94a3b8;
            border: none;
            border-radius: 999px;
            padding: 5px 14px;
            font-size: 12px;
            font-weight: 600;
            box-shadow: none;
            transition: background 120ms ease, color 120ms ease;
        }
        .settings-stack-switcher button:hover {
            background: rgba(255, 255, 255, 0.06);
            color: #f1f5f9;
        }
        .settings-stack-switcher button:checked {
            background: #6366f1;
            color: #ffffff;
            box-shadow: 0 2px 8px rgba(99, 102, 241, 0.35);
        }
        .settings-action-btn {
            background: rgba(255, 255, 255, 0.05);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 600;
        }
        .settings-action-btn:hover {
            background: rgba(255, 255, 255, 0.10);
            border-color: rgba(255, 255, 255, 0.22);
            color: #ffffff;
        }
        .settings-danger-btn {
            background: rgba(239, 68, 68, 0.12);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.3);
            border-radius: 8px;
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 600;
        }
        .settings-danger-btn:hover {
            background: rgba(239, 68, 68, 0.22);
            border-color: rgba(239, 68, 68, 0.5);
            color: #fecaca;
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
        if self.dark_mode_active:
            self._set_dark_stylesheet(ucm, True)

        # Signals
        webview.connect("load-changed", self.on_load_changed)
        webview.connect("mouse-target-changed", self.on_mouse_target_changed)
        webview.connect("notify::title", self.on_webview_title_notify)
        webview.connect("resource-load-started", self.on_resource_load_started)
        webview.connect("web-process-terminated", self.on_web_process_terminated)
        webview.connect("permission-request", self.on_permission_request)
        webview.connect("load-failed-with-tls-errors", self.on_load_failed_with_tls_errors)
        webview.connect("load-failed", self.on_load_failed)
        # Files WebKit can't render inline (Office docs, zip archives, etc.)
        # would otherwise just interrupt the frame load and surface as a
        # confusing "page didn't load" error; convert them into a normal
        # download instead, same as clicking a download link would do.
        webview.connect("decide-policy", self.on_decide_policy)
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

    SAVE_DEBOUNCE_SECONDS = 5

    def _schedule_history_save(self):
        """Coalesce bursts of history changes into one disk write."""
        if self._history_save_source is None:
            self._history_save_source = GLib.timeout_add_seconds(self.SAVE_DEBOUNCE_SECONDS, self._run_history_save)

    def _run_history_save(self):
        self._history_save_source = None
        if not self.is_private:
            save_url_history(self.url_history)
        return False

    def _schedule_session_save(self):
        if self._session_save_source is None:
            self._session_save_source = GLib.timeout_add_seconds(self.SAVE_DEBOUNCE_SECONDS, self._run_session_save)

    def _collect_session_urls(self):
        urls = []
        for i in range(self.notebook.get_n_pages()):
            tb = self.notebook.get_nth_page(i)
            if hasattr(tb, '_bharat_webview'):
                # A suspended tab's live webview URI is "about:blank"; use the
                # URI it was suspended at so its session-restore entry survives.
                u = self._suspended_tab_uris.get(id(tb)) or tb._bharat_webview.get_uri()
                if u and not u.startswith("about:"):
                    urls.append(u)
        return urls

    def _run_session_save(self):
        self._session_save_source = None
        if not self.is_private:
            urls = self._collect_session_urls()
            if urls:
                save_session_state(urls)
        return False

    def _flush_pending_saves(self):
        """Write anything still waiting on a debounce timer (used on close)."""
        if self._history_save_source is not None:
            GLib.source_remove(self._history_save_source)
            self._run_history_save()
        if self._session_save_source is not None:
            GLib.source_remove(self._session_save_source)
            self._run_session_save()

    def on_window_destroy(self, window):
        for i in range(self.notebook.get_n_pages()):
            tab_box = self.notebook.get_nth_page(i)
            if hasattr(tab_box, '_bharat_webview'):
                self._flush_page_view(tab_box._bharat_webview)

        if not self.is_private and getattr(self, 'clear_history_on_exit', False):
            self.url_history = []
            if self._history_save_source is not None:
                GLib.source_remove(self._history_save_source)
                self._history_save_source = None
            save_url_history([])
            if hasattr(self, 'url_completion_store'):
                self.url_completion_store.clear()
            if hasattr(self, '_page_view_start'):
                self._page_view_start.clear()

        self._flush_pending_saves()

        global _LIVE_WINDOW_COUNT
        _LIVE_WINDOW_COUNT -= 1
        if _LIVE_WINDOW_COUNT <= 0:
            Gtk.main_quit()

    def close_tab(self, tab_box):
        if hasattr(tab_box, '_bharat_webview'):
            self._crash_counts.pop(id(tab_box._bharat_webview), None)
            self._load_failure_counts.pop(id(tab_box._bharat_webview), None)
            self._flush_page_view(tab_box._bharat_webview)
        self._tab_last_active.pop(id(tab_box), None)
        self._suspended_session_states.pop(id(tab_box), None)
        self._suspended_tab_titles.pop(id(tab_box), None)
        self._suspended_tab_uris.pop(id(tab_box), None)
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

    TAB_SUSPENSION_CHECK_INTERVAL_SECONDS = 60
    TAB_SUSPENSION_INACTIVE_SECONDS = 15 * 60  # 15 minutes in the background

    def on_tab_changed(self, notebook, page, page_num):
        if self.find_bar.get_visible():
            self.close_find_bar()

        # "page" is the tab_box widget being switched TO; whatever was
        # active before (tracked from the previous call) just became
        # background, so its inactivity clock starts now.
        previous_tab_box = getattr(self, '_current_active_tab_box', None)
        if previous_tab_box is not None:
            self._tab_last_active[id(previous_tab_box)] = time.monotonic()
        self._current_active_tab_box = page
        if page is not None:
            self._tab_last_active[id(page)] = time.monotonic()
            if id(page) in self._suspended_session_states:
                self._reactivate_tab(page)

        webview = self.get_active_webview()
        if webview:
            uri = webview.get_uri() or ""
            title = webview.get_title() or f"Bharat Browser v{self.current_version}"
            self.url_entry.set_text(uri)
            self.update_security_icon(uri)
            self.set_title(f"{title} - Bharat Browser v{self.current_version}")
            self.statusbar.push(self.context_id, f"Ready | {uri}")

    def _check_tab_suspension(self):
        if self.tab_suspension_enabled:
            now = time.monotonic()
            active_tab_box = getattr(self, '_current_active_tab_box', None)
            for i in range(self.notebook.get_n_pages()):
                tab_box = self.notebook.get_nth_page(i)
                if tab_box is active_tab_box or not hasattr(tab_box, '_bharat_webview'):
                    continue
                if id(tab_box) in self._suspended_session_states:
                    continue  # already suspended
                last_active = self._tab_last_active.get(id(tab_box))
                if last_active is None or (now - last_active) < self.TAB_SUSPENSION_INACTIVE_SECONDS:
                    continue
                webview = tab_box._bharat_webview
                # Never suspend a tab that's still loading or playing audio/
                # video — the whole point is not to disturb anything the
                # user might actually be paying attention to in the
                # background (a podcast tab, a download in progress, etc.).
                if webview.is_loading() or webview.is_playing_audio():
                    continue
                self._suspend_tab(tab_box)
        return True  # keep the periodic sweep running

    def _suspend_tab(self, tab_box):
        webview = tab_box._bharat_webview
        uri = webview.get_uri() or ""
        if not uri or uri.startswith("about:"):
            return  # nothing meaningful to save/restore
        self._suspended_session_states[id(tab_box)] = webview.get_session_state()
        self._suspended_tab_uris[id(tab_box)] = uri
        original_title = tab_box._bharat_label.get_text() if hasattr(tab_box, '_bharat_label') else uri
        self._suspended_tab_titles[id(tab_box)] = original_title
        if hasattr(tab_box, '_bharat_label'):
            tab_box._bharat_label.set_text("💤 " + original_title)
        webview.load_uri("about:blank")

    def _reactivate_tab(self, tab_box):
        session_state = self._suspended_session_states.pop(id(tab_box), None)
        self._suspended_tab_titles.pop(id(tab_box), None)
        self._suspended_tab_uris.pop(id(tab_box), None)
        if session_state is None:
            return
        webview = tab_box._bharat_webview
        webview.restore_session_state(session_state)
        back_forward_list = webview.get_back_forward_list()
        current_item = back_forward_list.get_current_item()
        if current_item is not None:
            webview.go_to_back_forward_list_item(current_item)

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
        self.update_bookmark_star(uri)

    # Bookmarks
    @staticmethod
    def _bookmarkable(uri):
        return bool(uri) and uri.startswith(("http://", "https://"))

    def _find_bookmark(self, uri):
        return next((b for b in self.bookmarks if b.get("url") == uri), None)

    def update_bookmark_star(self, uri):
        """Address-bar star: filled when the current page is bookmarked.
        Hidden for pages that can't be bookmarked (about:, data:, the history
        dashboard) and in private windows, which never write bookmarks."""
        pos = Gtk.EntryIconPosition.SECONDARY
        if self.is_private or not self._bookmarkable(uri):
            self.url_entry.set_icon_from_icon_name(pos, None)
            return
        if self._find_bookmark(uri):
            self.url_entry.set_icon_from_icon_name(pos, "starred-symbolic")
            self.url_entry.set_icon_tooltip_text(pos, "Remove bookmark (Ctrl+D)")
        else:
            self.url_entry.set_icon_from_icon_name(pos, "non-starred-symbolic")
            self.url_entry.set_icon_tooltip_text(pos, "Bookmark this page (Ctrl+D)")
        self.url_entry.set_icon_activatable(pos, True)

    def on_url_entry_icon_press(self, entry, icon_pos, event):
        if icon_pos == Gtk.EntryIconPosition.SECONDARY:
            self.toggle_bookmark_current()

    def toggle_bookmark_current(self):
        if self.is_private:
            self.statusbar.push(self.context_id, "Bookmarks aren't saved in private windows")
            return
        webview = self.get_active_webview()
        uri = (webview.get_uri() or "") if webview else ""
        if not self._bookmarkable(uri):
            self.statusbar.push(self.context_id, "This page can't be bookmarked")
            return
        existing = self._find_bookmark(uri)
        if existing:
            self.bookmarks.remove(existing)
            self.statusbar.push(self.context_id, "☆ Bookmark removed")
        else:
            if len(self.bookmarks) >= BOOKMARKS_MAX_ENTRIES:
                self.statusbar.push(self.context_id, f"Bookmark limit ({BOOKMARKS_MAX_ENTRIES}) reached; remove some first")
                return
            title = self._resolve_display_title(webview)
            self.bookmarks.append({"url": uri, "title": title, "added": time.time()})
            self.statusbar.push(self.context_id, f"⭐ Bookmarked: {title}")
        save_bookmarks(self.bookmarks)
        self.update_bookmark_star(uri)

    def open_bookmark_manager(self):
        if self.is_private:
            self.statusbar.push(self.context_id, "Bookmarks aren't available in private windows")
            return
        dialog = Gtk.Dialog(title="⭐ Bookmark Manager", transient_for=self, modal=True, destroy_with_parent=True)
        dialog.get_style_context().add_class("bharat-dialog")
        self.apply_dark_titlebar(dialog, "⭐ Bookmark Manager")
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(680, 460)

        area = dialog.get_content_area()
        for setter in (area.set_margin_start, area.set_margin_end, area.set_margin_top, area.set_margin_bottom):
            setter(16)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        area.pack_start(vbox, True, True, 0)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search bookmarks…")
        vbox.pack_start(search, False, False, 0)

        # Columns: title, url, index into self.bookmarks (stable across filtering)
        store = Gtk.ListStore(str, str, int)
        def fill():
            store.clear()
            for i, b in enumerate(self.bookmarks):
                store.append([b.get("title") or b["url"], b["url"], i])
        fill()

        def visible(model, it, _data):
            q = search.get_text().strip().lower()
            return not q or q in model[it][0].lower() or q in model[it][1].lower()
        flt = store.filter_new()
        flt.set_visible_func(visible)
        search.connect("search-changed", lambda e: flt.refilter())

        tree = Gtk.TreeView(model=flt)
        tree.set_headers_visible(True)
        for col_title, col_idx, width in (("Title", 0, 40), ("URL", 1, 60)):
            cell = Gtk.CellRendererText()
            # Ellipsize: an unbounded very long URL would otherwise size the
            # column (and dialog) past GDK's window-size limit.
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(col_title, cell, text=col_idx)
            col.set_resizable(True)
            col.set_expand(True)
            tree.append_column(col)
        scroller = Gtk.ScrolledWindow()
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(tree)
        vbox.pack_start(scroller, True, True, 0)

        empty_lbl = Gtk.Label(label="No bookmarks yet. Click the ☆ in the address bar (or press Ctrl+D) on any page to add one.")
        empty_lbl.set_line_wrap(True)
        vbox.pack_start(empty_lbl, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_open = Gtk.Button(label="Open in New Tab")
        btn_rename = Gtk.Button(label="Rename…")
        btn_delete = Gtk.Button(label="Delete")
        for b in (btn_open, btn_rename, btn_delete):
            btn_row.pack_start(b, False, False, 0)
        vbox.pack_start(btn_row, False, False, 0)

        pending_open = []

        def selected_bookmark():
            model, it = tree.get_selection().get_selected()
            if it is None:
                return None
            return self.bookmarks[model[it][2]]

        def refresh_state():
            has_any = bool(self.bookmarks)
            empty_lbl.set_visible(not has_any)
            sel = tree.get_selection().get_selected()[1] is not None
            for b in (btn_open, btn_rename, btn_delete):
                b.set_sensitive(sel)

        def do_open(*_a):
            b = selected_bookmark()
            if b:
                pending_open.append(b["url"])
                dialog.response(Gtk.ResponseType.ACCEPT)

        def do_rename(*_a):
            b = selected_bookmark()
            if not b:
                return
            rd = Gtk.Dialog(title="Rename bookmark", transient_for=dialog, modal=True)
            rd.add_button("Cancel", Gtk.ResponseType.CANCEL)
            rd.add_button("Save", Gtk.ResponseType.OK)
            rd.set_default_response(Gtk.ResponseType.OK)
            entry = Gtk.Entry(text=b.get("title") or b["url"])
            entry.set_activates_default(True)
            entry.set_width_chars(48)
            ra = rd.get_content_area()
            ra.set_margin_start(16); ra.set_margin_end(16); ra.set_margin_top(16); ra.set_margin_bottom(8)
            ra.add(entry)
            rd.show_all()
            if rd.run() == Gtk.ResponseType.OK and entry.get_text().strip():
                b["title"] = entry.get_text().strip()
                save_bookmarks(self.bookmarks)
                fill()
            rd.destroy()
            refresh_state()

        def do_delete(*_a):
            b = selected_bookmark()
            if not b:
                return
            self.bookmarks.remove(b)
            save_bookmarks(self.bookmarks)
            fill()
            refresh_state()
            webview = self.get_active_webview()
            if webview:
                self.update_bookmark_star(webview.get_uri() or "")

        btn_open.connect("clicked", do_open)
        btn_rename.connect("clicked", do_rename)
        btn_delete.connect("clicked", do_delete)
        tree.connect("row-activated", do_open)
        tree.get_selection().connect("changed", lambda sel: refresh_state())

        dialog.show_all()
        refresh_state()
        empty_lbl.set_visible(not self.bookmarks)
        result = dialog.run()
        dialog.destroy()
        if result == Gtk.ResponseType.ACCEPT and pending_open:
            self.create_new_tab(pending_open[0])

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

    def on_decide_policy(self, webview, decision, decision_type):
        # Only response decisions (i.e. we already have headers back and
        # know the content type) carry is_mime_type_supported(); navigation/
        # new-window decisions don't have a response yet, so leave those to
        # WebKit's own default handling.
        if decision_type != WebKit2.PolicyDecisionType.RESPONSE:
            return False
        if not decision.is_mime_type_supported():
            # .doc/.docx/.xls/.xlsx and similar have no in-browser renderer
            # (unlike PDF, which WebKit renders natively) — download instead
            # of leaving the user with an interrupted, blank frame.
            decision.download()
            return True
        return False

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
        # Also expected/benign: on_decide_policy() deliberately converts any
        # response WebKit can't render (Office docs, archives, etc.) into a
        # download, which itself interrupts the frame load that would have
        # displayed it. Without this, that expected interruption would count
        # as a real failure and eventually show a "page didn't load" error
        # for a download that actually succeeded.
        if error.matches(WebKit2.policy_error_quark(), WebKit2.PolicyError.FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE):
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
            elif shift and keyval in (Gdk.KEY_o, Gdk.KEY_O):
                self.open_bookmark_manager()
                return True
            elif keyval in (Gdk.KEY_d, Gdk.KEY_D):
                self.toggle_bookmark_current()
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
    def default_downloads_dir(self):
        xdg_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        downloads_dir = xdg_dir or os.path.expanduser("~/Downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        return downloads_dir

    def get_downloads_dir(self):
        """The user's chosen folder (Downloads Manager > Change Folder), or the
        system default. A chosen folder that has since been deleted is
        recreated; one that can't be written to (unmounted drive, changed
        permissions) falls back to the default instead of failing every
        download."""
        custom = os.path.expanduser(self.download_dir) if self.download_dir else ""
        if custom:
            try:
                os.makedirs(custom, exist_ok=True)
                if os.access(custom, os.W_OK | os.X_OK):
                    return custom
            except OSError as e:
                print("Download folder note:", e)
            self.statusbar.push(self.context_id, f"⚠️ Download folder {custom} is not writable; using the default folder")
        return self.default_downloads_dir()

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
        dialog.set_default_size(520, 400)

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

        lbl_folder_title = Gtk.Label(label="Save downloads to:", xalign=0.0)
        vbox.pack_start(lbl_folder_title, False, False, 0)
        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_folder = Gtk.Label(label=self.get_downloads_dir(), xalign=0.0)
        lbl_folder.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        lbl_folder.set_selectable(True)
        folder_row.pack_start(lbl_folder, True, True, 0)
        btn_change_folder = Gtk.Button(label="Change…")
        btn_reset_folder = Gtk.Button(label="Reset")
        folder_row.pack_start(btn_change_folder, False, False, 0)
        folder_row.pack_start(btn_reset_folder, False, False, 0)
        vbox.pack_start(folder_row, False, False, 0)

        def refresh_folder_label():
            lbl_folder.set_text(self.get_downloads_dir())
            btn_reset_folder.set_sensitive(bool(self.download_dir))

        def on_change_folder(_btn):
            chooser = Gtk.FileChooserDialog(
                title="Choose download folder",
                transient_for=dialog,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            chooser.add_button("Cancel", Gtk.ResponseType.CANCEL)
            chooser.add_button("Select", Gtk.ResponseType.ACCEPT)
            chooser.set_create_folders(True)
            chooser.set_current_folder(self.get_downloads_dir())
            if chooser.run() == Gtk.ResponseType.ACCEPT:
                chosen = chooser.get_filename()
                if chosen and os.access(chosen, os.W_OK | os.X_OK):
                    self.download_dir = chosen
                    self.save_settings()
                    self.statusbar.push(self.context_id, f"📁 Downloads will be saved to {chosen}")
                else:
                    self.statusbar.push(self.context_id, "⚠️ That folder isn't writable; download folder unchanged")
            chooser.destroy()
            refresh_folder_label()

        def on_reset_folder(_btn):
            self.download_dir = ""
            self.save_settings()
            refresh_folder_label()

        btn_change_folder.connect("clicked", on_change_folder)
        btn_reset_folder.connect("clicked", on_reset_folder)
        btn_reset_folder.set_sensitive(bool(self.download_dir))

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

    def check_for_updates_interactive(self, status_lbl, btn_check, btn_restart):
        """User-triggered update check from Settings. Updates the status label,
        downloads and installs the update if available, and offers a restart button."""
        btn_check.set_sensitive(False)
        status_lbl.set_markup("<i>🔄 Connecting to GitHub and checking for updates...</i>")
        btn_restart.hide()

        def _worker():
            try:
                url = "https://raw.githubusercontent.com/Sangam1112/bharat-browser/master/package.json"
                req = urllib.request.Request(url, headers={"User-Agent": f"BharatBrowser/{self.current_version}"})
                with urllib.request.urlopen(req, timeout=8) as response:
                    if response.status != 200:
                        GLib.idle_add(_done, f"❌ Server returned HTTP {response.status}", False, True)
                        return
                    data = json.loads(response.read(1 << 20).decode('utf-8'))
                    remote_version = data.get("version", "").strip()
                    remote_sha256 = data.get("sha256", "").strip().lower()

                if not remote_version or self.compare_versions(remote_version, self.current_version) <= 0:
                    GLib.idle_add(_done, f"✅ Bharat Browser is up to date (v{self.current_version}).", False, True)
                    return

                GLib.idle_add(lambda: status_lbl.set_markup(f"<i>⬇️ New version v{remote_version} found! Downloading update...</i>"))
                installed = self.download_and_install_update(remote_version, remote_sha256)
                if installed:
                    GLib.idle_add(_done, f"🎉 Version v{remote_version} installed successfully! Click 'Restart Now' to apply.", True, True)
                    GLib.idle_add(self.show_update_notification_dialog, remote_version, True)
                else:
                    GLib.idle_add(_done, f"⬆️ Version v{remote_version} is available on GitHub (Install via package manager or git pull).", False, True)
                    GLib.idle_add(self.show_update_notification_dialog, remote_version, False)
            except Exception as e:
                GLib.idle_add(_done, f"❌ Update check failed: {str(e)}", False, True)

        def _done(msg, show_restart, enable_btn):
            status_lbl.set_markup(f"<b>{GLib.markup_escape_text(msg)}</b>" if "✅" in msg or "🎉" in msg else GLib.markup_escape_text(msg))
            btn_check.set_sensitive(enable_btn)
            if show_restart:
                btn_restart.show()

        threading.Thread(target=_worker, daemon=True).start()

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
        # os.execv replaces the process image directly — no "destroy" signal
        # fires, so the active tab's in-progress time-on-page would
        # otherwise never reach the History Dashboard for this session.
        for i in range(self.notebook.get_n_pages()):
            tab_box = self.notebook.get_nth_page(i)
            if hasattr(tab_box, '_bharat_webview'):
                self._flush_page_view(tab_box._bharat_webview)
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

    # DNS pre-resolution: warm WebKit's resolver for a host the user is likely
    # to visit next, shaving the lookup off the real navigation. Resolution
    # only, no connection or request is made to the site.
    DNS_PREFETCH_TYPING_DELAY_MS = 400
    DNS_PREFETCH_CACHE_MAX = 256

    def _prefetch_dns(self, host):
        host = (host or "").strip().rstrip(".").lower()
        if not host or host in self._dns_prefetched or is_local_network_host(host):
            return
        try:
            ipaddress.ip_address(host.strip("[]"))
            return
        except ValueError:
            pass
        if len(self._dns_prefetched) >= self.DNS_PREFETCH_CACHE_MAX:
            self._dns_prefetched.clear()
        self._dns_prefetched.add(host)
        try:
            self.context.prefetch_dns(host)
        except Exception as e:
            print("DNS prefetch note:", e)

    def on_mouse_target_changed(self, webview, hit_test_result, modifiers):
        if not hit_test_result.context_is_link():
            return
        uri = hit_test_result.get_link_uri() or ""
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            self._prefetch_dns(parsed.hostname)

    def on_url_entry_changed(self, entry):
        # Private windows never resolve half-typed text: it would leak
        # keystrokes to the DNS resolver for pages never visited.
        if self.is_private:
            return
        if self._dns_typing_source is not None:
            GLib.source_remove(self._dns_typing_source)
        self._dns_typing_source = GLib.timeout_add(self.DNS_PREFETCH_TYPING_DELAY_MS, self._run_typing_dns_prefetch)

    def _run_typing_dns_prefetch(self):
        self._dns_typing_source = None
        text = self.url_entry.get_text().strip()
        if not text or " " in text or text.startswith("about:"):
            return False
        parsed = urllib.parse.urlparse(text if "://" in text else "//" + text)
        host = parsed.hostname or ""
        # Only names that already look like a full domain (a dot, and a
        # TLD-length tail) — "git" or "github." would be wasted lookups.
        if "." in host and len(host.rsplit(".", 1)[-1]) >= 2:
            self._prefetch_dns(host)
        return False

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
        self._schedule_history_save()

        # Update the autocomplete model in place instead of clearing and
        # re-appending every entry on each page load.
        store = self.url_completion_store
        it = store.get_iter_first()
        while it is not None:
            nxt = store.iter_next(it)
            if store.get_value(it, 0) == url:
                store.remove(it)
                break
            it = nxt
        store.prepend([url, title or url])
        while len(store) > HISTORY_MAX_ENTRIES:
            store.remove(store.get_iter(len(store) - 1))

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
                self._schedule_history_save()
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
            <tr data-url="{GLib.markup_escape_text(url)}">
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
    <script>
        // Delegated listener reading dataset.url (the raw attribute value,
        // not JS source) instead of an inline onclick="...'{{url}}'..." —
        // avoids ever needing to escape a URL for a JS string-literal
        // context (GLib.markup_escape_text() above only guarantees safe
        // HTML-attribute escaping, not JS-string escaping).
        document.querySelectorAll('tr[data-url]').forEach(function(row) {{
            row.addEventListener('click', function() {{
                window.location.href = row.dataset.url;
            }});
        }});
    </script>
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

    def _resolve_display_title(self, webview):
        title = webview.get_title()
        if not title:
            # No <title> to report (WebKit's PDF.js-based in-tab viewer, a
            # directly-opened image, etc.) — fall back to the URL's filename
            # instead of leaving the tab stuck on a generic placeholder.
            uri = webview.get_uri() or ""
            title = os.path.basename(urllib.parse.urlparse(uri).path.rstrip('/'))
        return title or "New Tab"

    def _apply_display_title(self, webview):
        title = self._resolve_display_title(webview)
        for i in range(self.notebook.get_n_pages()):
            tab_box = self.notebook.get_nth_page(i)
            if hasattr(tab_box, '_bharat_webview') and tab_box._bharat_webview == webview:
                if hasattr(tab_box, '_bharat_label'):
                    # A suspended tab's webview is showing "about:blank"
                    # while suspended, which would otherwise overwrite the
                    # sleep-indicator label with an unhelpful fallback the
                    # instant that load's own title-update events fire.
                    if id(tab_box) in self._suspended_tab_titles:
                        tab_box._bharat_label.set_text("💤 " + self._suspended_tab_titles[id(tab_box)])
                    else:
                        tab_box._bharat_label.set_text(title)
                break
        if self.get_active_webview() == webview:
            self.set_title(f"{title} - Bharat Browser v{self.current_version}")
        self._update_history_title(webview, title)

    def _update_history_title(self, webview, title):
        """record_history_entry() runs at LoadEvent.FINISHED, when the real
        <title> often isn't known yet (see _resolve_display_title), so the
        history/autocomplete entry can get stuck on the URL-filename
        fallback forever. Called from _apply_display_title too, so once the
        real title does arrive via notify::title, the stored entry gets
        corrected the same way the tab label and window title already do."""
        if self.is_private:
            return
        uri = webview.get_uri() or ""
        if not uri or uri.startswith("about:"):
            return
        for entry in self.url_history:
            if entry.get("url") == uri:
                if entry.get("title") != title:
                    entry["title"] = title
                    self._schedule_history_save()
                    for row in self.url_completion_store:
                        if row[0] == uri:
                            row[1] = title
                            break
                break

    def on_webview_title_notify(self, webview, pspec):
        self._apply_display_title(webview)

    def on_load_changed(self, webview, load_event):
        if load_event == WebKit2.LoadEvent.STARTED:
            self.statusbar.push(self.context_id, "Loading webpage...")
        elif load_event == WebKit2.LoadEvent.FINISHED:
            self._crash_counts.pop(id(webview), None)
            uri = webview.get_uri() or ""
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
                self.statusbar.push(self.context_id, f"Ready | {uri}")

            # get_title() is often still empty at this exact instant — WebKit
            # sets it slightly later via "notify::title" (confirmed: title is
            # None at FINISHED, arrives ~200ms after even for a trivial local
            # page). _apply_display_title() runs now for an immediate
            # best-effort label, and the notify::title handler below corrects
            # it once the real title (or, for the PDF.js viewer, no title —
            # hence the filename fallback in _resolve_display_title) is known.
            self._apply_display_title(webview)

            # Save session state across tabs (skipped for private windows)
            if not self.is_private:
                self._schedule_session_save()

                self._flush_page_view(webview)
                self.record_history_entry(uri, self._resolve_display_title(webview))
                self._start_page_view(webview, uri)

            # Dark mode needs no per-load work: the UserStyleSheet added in
            # create_new_tab()/apply_dark_reader_to_webview() applies to every
            # document this webview loads.

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
            "first_run_greeted": self.first_run_greeted,
            "low_memory_mode": self.low_memory_mode,
            "tab_suspension_enabled": self.tab_suspension_enabled,
            "clear_history_on_exit": self.clear_history_on_exit,
            "gpu_acceleration_enabled": self.gpu_acceleration_enabled,
            "download_dir": self.download_dir
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

    def _set_dark_stylesheet(self, ucm, enabled):
        # Flag lives on the content manager, not the webview: a popup opened
        # via on_create_webview() shares its opener's manager, and adding or
        # removing the same sheet twice there would leave it half-applied.
        if getattr(ucm, "_bharat_dark_applied", False) == enabled:
            return
        if enabled:
            ucm.add_style_sheet(self.dark_stylesheet)
        else:
            ucm.remove_style_sheet(self.dark_stylesheet)
        ucm._bharat_dark_applied = enabled

    def apply_dark_reader_to_webview(self, webview):
        self._set_dark_stylesheet(webview.get_user_content_manager(), True)

    def remove_dark_reader_from_webview(self, webview):
        self._set_dark_stylesheet(webview.get_user_content_manager(), False)

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
    def _create_setting_card(title_text=None):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("settings-card")
        if title_text:
            title_lbl = Gtk.Label(xalign=0.0)
            title_lbl.set_markup(f"<b>{GLib.markup_escape_text(title_text)}</b>")
            title_lbl.get_style_context().add_class("settings-section-title")
            card.pack_start(title_lbl, False, False, 0)
            card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
        return card

    @staticmethod
    def _create_toggle_row(title_text, subtitle_text, is_active, on_toggled_cb):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("settings-row")

        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_lbl = Gtk.Label(xalign=0.0)
        title_lbl.set_markup(f"<span weight='semibold'>{GLib.markup_escape_text(title_text)}</span>")
        title_lbl.get_style_context().add_class("settings-row-title")
        text_vbox.pack_start(title_lbl, False, False, 0)

        if subtitle_text:
            sub_lbl = Gtk.Label(xalign=0.0)
            sub_lbl.set_markup(f"<small>{GLib.markup_escape_text(subtitle_text)}</small>")
            sub_lbl.get_style_context().add_class("settings-hint-label")
            sub_lbl.set_line_wrap(True)
            text_vbox.pack_start(sub_lbl, False, False, 0)

        row.pack_start(text_vbox, True, True, 0)

        chk = Gtk.CheckButton()
        chk.set_active(bool(is_active))
        chk.set_valign(Gtk.Align.CENTER)
        if on_toggled_cb:
            chk.connect("toggled", lambda cb: on_toggled_cb(cb.get_active()))
        row.pack_end(chk, False, False, 0)
        return row

    def on_settings_clicked(self, btn):
        title_text = f"Bharat Browser Settings (v{self.current_version})"
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
        dialog.set_default_size(580, 560)

        content_area = dialog.get_content_area()
        content_area.set_margin_start(16)
        content_area.set_margin_end(16)
        content_area.set_margin_top(12)
        content_area.set_margin_bottom(12)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_area.pack_start(main_box, True, True, 0)

        # Tab navigation with Gtk.Stack and Gtk.StackSwitcher
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(200)

        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        stack_switcher.get_style_context().add_class("settings-stack-switcher")
        main_box.pack_start(stack_switcher, False, False, 0)
        main_box.pack_start(stack, True, True, 0)

        def _create_scrollable_page():
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            page_box.set_margin_start(4)
            page_box.set_margin_end(4)
            page_box.set_margin_top(8)
            page_box.set_margin_bottom(8)
            scroller.add(page_box)
            return scroller, page_box

        # --- Tab 1: General -----------------------------------------------
        gen_scroller, gen_box = _create_scrollable_page()

        search_card = self._create_setting_card("SEARCH ENGINE")
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        search_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        s_title = Gtk.Label(xalign=0.0)
        s_title.set_markup("<span weight='semibold'>🔍 Default Search Engine</span>")
        s_title.get_style_context().add_class("settings-row-title")
        search_lbl_box.pack_start(s_title, False, False, 0)
        s_hint = Gtk.Label(xalign=0.0)
        s_hint.set_markup("<small>Used when searching directly from the address bar.</small>")
        s_hint.get_style_context().add_class("settings-hint-label")
        search_lbl_box.pack_start(s_hint, False, False, 0)
        search_row.pack_start(search_lbl_box, True, True, 0)

        search_combo = Gtk.ComboBoxText()
        for engine_name in SEARCH_ENGINES:
            search_combo.append_text(engine_name)
        search_combo.set_active(list(SEARCH_ENGINES.keys()).index(self.search_engine))
        search_combo.connect("changed", lambda cb: (setattr(self, 'search_engine', cb.get_active_text()), self.save_settings()))
        search_combo.set_valign(Gtk.Align.CENTER)
        search_row.pack_end(search_combo, False, False, 0)
        search_card.pack_start(search_row, False, False, 0)
        gen_box.pack_start(search_card, False, False, 0)

        home_card = self._create_setting_card("HOMEPAGE & STARTUP")
        home_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        h_title = Gtk.Label(xalign=0.0)
        h_title.set_markup("<span weight='semibold'>🏠 Custom Homepage</span>")
        h_title.get_style_context().add_class("settings-row-title")
        home_lbl_box.pack_start(h_title, False, False, 0)
        h_hint = Gtk.Label(xalign=0.0)
        h_hint.set_markup("<small>Loaded on new tabs (Ctrl+T) and initial startup.</small>")
        h_hint.get_style_context().add_class("settings-hint-label")
        home_lbl_box.pack_start(h_hint, False, False, 0)
        home_card.pack_start(home_lbl_box, False, False, 0)

        home_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        home_entry = Gtk.Entry()
        home_entry.set_text(self.homepage)
        home_entry.set_placeholder_text("e.g. example.com or https://example.com")
        home_entry.set_hexpand(True)
        home_box.pack_start(home_entry, True, True, 0)

        btn_set_home = Gtk.Button(label="Set")
        btn_set_home.get_style_context().add_class("settings-action-btn")
        btn_set_home.set_tooltip_text("Save this address as your homepage")
        home_box.pack_start(btn_set_home, False, False, 0)

        btn_reset_home = Gtk.Button(label="Use Google")
        btn_reset_home.get_style_context().add_class("settings-action-btn")
        btn_reset_home.set_tooltip_text(f"Reset homepage to {DEFAULT_HOMEPAGE}")
        home_box.pack_start(btn_reset_home, False, False, 0)
        home_card.pack_start(home_box, False, False, 0)

        home_status = Gtk.Label(xalign=0.0)
        home_status.get_style_context().add_class("settings-hint-label")
        home_card.pack_start(home_status, False, False, 0)

        def _apply_homepage(new_value):
            self.homepage = sanitize_homepage_url(new_value)
            home_entry.set_text(self.homepage)
            self.save_settings()
            home_status.set_text(f"✅ Homepage saved: {self.homepage}")
            GLib.timeout_add(3000, lambda: (home_status.set_text(""), False)[1])

        btn_set_home.connect("clicked", lambda b: _apply_homepage(home_entry.get_text()))
        home_entry.connect("activate", lambda e: _apply_homepage(e.get_text()))
        btn_reset_home.connect("clicked", lambda b: _apply_homepage(DEFAULT_HOMEPAGE))

        home_card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
        home_card.pack_start(
            self._create_toggle_row(
                "🚀 Open homepage on startup",
                "When enabled, browser opens your homepage. When disabled, restores previous open tabs.",
                self.open_homepage_on_startup,
                lambda act: (setattr(self, 'open_homepage_on_startup', act), self.save_settings())
            ),
            False, False, 0
        )
        gen_box.pack_start(home_card, False, False, 0)
        stack.add_titled(gen_scroller, "general", "🌐 General")

        # --- Tab 2: Privacy & Security ------------------------------------
        priv_scroller, priv_box = _create_scrollable_page()

        shield_card = self._create_setting_card("SHIELD & PROTECTION")
        shield_card.pack_start(
            self._create_toggle_row(
                "🛡️ Ad & Tracker Blocking",
                "Blocks known ad networks, telemetry, and tracking scripts with high throughput.",
                self.adblock_enabled,
                lambda act: (setattr(self, 'adblock_enabled', act), self.save_settings())
            ),
            False, False, 0
        )
        shield_card.pack_start(
            self._create_toggle_row(
                "🔗 Strip Tracking Parameters (ClearURLs)",
                "Removes utm_*, fbclid, gclid, and other analytics parameters from URLs.",
                self.clearurls_enabled,
                lambda act: (setattr(self, 'clearurls_enabled', act), self.save_settings())
            ),
            False, False, 0
        )
        shield_card.pack_start(
            self._create_toggle_row(
                "🔒 HTTPS Enforcement",
                "Automatically upgrades unencrypted HTTP connections to secure HTTPS.",
                self.https_enabled,
                lambda act: (setattr(self, 'https_enabled', act), self.save_settings())
            ),
            False, False, 0
        )
        shield_card.pack_start(
            self._create_toggle_row(
                "🎥 WebRTC / Camera & Mic Access",
                "Off by default — WebRTC can leak your real local IP address even when using VPN.",
                self.webrtc_enabled,
                lambda act: self.on_webrtc_toggled(act)
            ),
            False, False, 0
        )
        priv_box.pack_start(shield_card, False, False, 0)

        hist_card = self._create_setting_card("HISTORY & DISPLAY PRIVACY")
        hist_card.pack_start(
            self._create_toggle_row(
                "🧹 Clear Browsing History on Exit",
                "Automatically wipes browsing history and autocomplete suggestions when exiting.",
                self.clear_history_on_exit,
                lambda act: (setattr(self, 'clear_history_on_exit', act), self.save_settings())
            ),
            False, False, 0
        )
        hist_card.pack_start(
            self._create_toggle_row(
                "🌙 DarkReader High-Contrast Engine",
                "Applies smart dark contrast stylesheets to all visited webpages.",
                self.dark_mode_active,
                lambda act: self.on_dark_clicked(self.btn_dark)
            ),
            False, False, 0
        )
        priv_box.pack_start(hist_card, False, False, 0)
        stack.add_titled(priv_scroller, "privacy", "🛡️ Privacy")

        # --- Tab 3: Performance & Advanced --------------------------------
        perf_scroller, perf_box = _create_scrollable_page()

        perf_card = self._create_setting_card("RESOURCE OPTIMIZATION")
        perf_card.pack_start(
            self._create_toggle_row(
                "😴 Suspend Inactive Background Tabs",
                "Frees memory by unloading background tabs inactive for 15+ minutes while keeping history intact.",
                self.tab_suspension_enabled,
                lambda act: (setattr(self, 'tab_suspension_enabled', act), self.save_settings())
            ),
            False, False, 0
        )
        perf_card.pack_start(
            self._create_toggle_row(
                "🪶 Low Memory Mode",
                "Shrinks WebKit page cache size immediately for systems with limited RAM.",
                self.low_memory_mode,
                lambda act: self.on_low_memory_mode_toggled(act)
            ),
            False, False, 0
        )
        perf_box.pack_start(perf_card, False, False, 0)

        gpu_card = self._create_setting_card("GPU ACCELERATION")
        gpu_detected_lbl = Gtk.Label(xalign=0.0)
        gpu_detected_lbl.set_markup(f"<small>Detected: {GLib.markup_escape_text(self.gpu_info_label)}</small>")
        gpu_detected_lbl.get_style_context().add_class("settings-hint-label")
        gpu_detected_lbl.set_line_wrap(True)
        gpu_card.pack_start(gpu_detected_lbl, False, False, 0)
        gpu_card.pack_start(
            self._create_toggle_row(
                "🎮 Use GPU for Rendering",
                "Offloads page compositing, canvas, and WebGL to the GPU instead of system RAM. Turn off only if pages render incorrectly (software fallback, uses more RAM/CPU).",
                self.gpu_acceleration_enabled,
                lambda act: self.on_gpu_acceleration_toggled(act)
            ),
            False, False, 0
        )
        perf_box.pack_start(gpu_card, False, False, 0)

        dev_card = self._create_setting_card("DEVELOPER TOOLS")
        dev_card.pack_start(
            self._create_toggle_row(
                "🛠️ Developer Tools (Web Inspector)",
                "Enables right-click Inspect Element and developer debugging tools.",
                self.dev_tools_enabled,
                lambda act: self.on_devtools_toggled(act)
            ),
            False, False, 0
        )
        perf_box.pack_start(dev_card, False, False, 0)
        stack.add_titled(perf_scroller, "performance", "⚡ Performance")

        # --- Tab 4: Data & Actions ----------------------------------------
        act_scroller, act_box = _create_scrollable_page()

        data_card = self._create_setting_card("BROWSING DATA MANAGEMENT")
        btn_hist = Gtk.Button(label="📊 Open Browsing History Dashboard (Ctrl+H)")
        btn_hist.get_style_context().add_class("settings-action-btn")
        btn_hist.connect("clicked", lambda b: (self.open_history_tab(), dialog.destroy()))
        data_card.pack_start(btn_hist, False, False, 0)

        btn_clear = Gtk.Button(label="🗑️ Clear Browsing History & Cookies Now")
        btn_clear.get_style_context().add_class("settings-danger-btn")
        btn_clear.connect("clicked", self.on_clear_cache_clicked)
        data_card.pack_start(btn_clear, False, False, 0)
        act_box.pack_start(data_card, False, False, 0)

        window_card = self._create_setting_card("WINDOW & NAVIGATION")
        btn_private = Gtk.Button(label="🕵 Open New Private Window (Ctrl+Shift+N)")
        btn_private.get_style_context().add_class("settings-action-btn")
        btn_private.connect("clicked", lambda b: (self.open_private_window(), dialog.destroy()))
        window_card.pack_start(btn_private, False, False, 0)
        act_box.pack_start(window_card, False, False, 0)

        about_card = self._create_setting_card("UPDATES & ABOUT BHARAT BROWSER")
        about_lbl = Gtk.Label(xalign=0.0)
        about_lbl.set_markup(
            f"<b>Bharat Browser v{self.current_version}</b>\n"
            f"<small>Modern, Ultra-Fast &amp; Privacy-First Linux Browser\n"
            f"Engineered in INDIA 🇮🇳</small>"
        )
        about_lbl.get_style_context().add_class("settings-hint-label")
        about_card.pack_start(about_lbl, False, False, 0)

        about_card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        update_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        btn_check_update = Gtk.Button(label="🔄 Check for Updates on GitHub")
        btn_check_update.get_style_context().add_class("settings-action-btn")
        update_vbox.pack_start(btn_check_update, False, False, 0)

        update_status_lbl = Gtk.Label(xalign=0.0)
        update_status_lbl.get_style_context().add_class("settings-hint-label")
        update_status_lbl.set_line_wrap(True)
        update_vbox.pack_start(update_status_lbl, False, False, 0)

        btn_restart_applied = Gtk.Button(label="🚀 Restart Now to Apply Update")
        btn_restart_applied.get_style_context().add_class("settings-action-btn")
        btn_restart_applied.set_no_show_all(True)
        btn_restart_applied.hide()
        btn_restart_applied.connect("clicked", lambda b: (dialog.destroy(), self.restart_application()))
        update_vbox.pack_start(btn_restart_applied, False, False, 0)

        btn_check_update.connect(
            "clicked",
            lambda b: self.check_for_updates_interactive(update_status_lbl, btn_check_update, btn_restart_applied)
        )
        about_card.pack_start(update_vbox, False, False, 0)
        act_box.pack_start(about_card, False, False, 0)

        stack.add_titled(act_scroller, "actions", "🗄️ Data & Actions")

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def on_devtools_toggled(self, active):
        self.dev_tools_enabled = active
        self.web_settings.set_enable_developer_extras(active)
        self.save_settings()

    def on_low_memory_mode_toggled(self, active):
        self.low_memory_mode = active
        self.save_settings()
        # Cache model applies to the already-running WebKitWebContext
        # immediately. WEBKIT_USE_SINGLE_WEB_PROCESS is also set for next
        # launch (see the startup env-var logic near the top of the file),
        # but empirically confirmed NOT honored on WebKitGTK 2.54 (4 tabs
        # still spawned 4 separate WebProcess instances with it set to "1")
        # — left in place in case it does something on other WebKit
        # versions, but the cache-size reduction below is the verified
        # effect, which is why it's the only one advertised in the UI text.
        if hasattr(WebKit2, 'CacheModel') and hasattr(WebKit2.CacheModel, 'WEB_BROWSER'):
            model = WebKit2.CacheModel.DOCUMENT_VIEWER if active else WebKit2.CacheModel.WEB_BROWSER
            self.context.set_cache_model(model)
        self.statusbar.push(
            self.context_id,
            "🪶 Low Memory Mode " + ("enabled" if active else "disabled")
        )

    def on_webrtc_toggled(self, active):
        self.webrtc_enabled = active
        self.web_settings.set_enable_webrtc(active)
        self.web_settings.set_enable_media_stream(active)
        self.save_settings()

    def on_gpu_acceleration_toggled(self, active):
        self.gpu_acceleration_enabled = active
        self.web_settings.set_hardware_acceleration_policy(
            WebKit2.HardwareAccelerationPolicy.ALWAYS if active
            else WebKit2.HardwareAccelerationPolicy.NEVER
        )
        self.web_settings.set_enable_webgl(active)
        if hasattr(self.web_settings, 'set_enable_2d_canvas_acceleration'):
            self.web_settings.set_enable_2d_canvas_acceleration(active)
        self.save_settings()
        self.statusbar.push(
            self.context_id,
            "🎮 GPU Acceleration " + ("enabled" if active else "disabled — using software rendering")
        )

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
    # Quitting is handled by on_window_destroy() (connected in __init__),
    # which only calls Gtk.main_quit() once every open top-level window
    # (main + any private windows) has actually closed.
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
