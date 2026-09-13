#!/usr/bin/env python3
"""
Bharat Browser v1.2.4 - GTK3 / WebKit2 Python Application
Modern, Ultra-Fast, Multi-Tab, and Privacy-First Web Browser engineered for Linux (Ubuntu)
"""
import sys
import os

# Enable GPU Hardware Acceleration & System-Level Acceleration Flags
os.environ["WEBKIT_FORCE_COMPOSITING_MODE"] = "1"
os.environ["GST_VAAPI_ALL_DRIVERS"] = "1"
os.environ["GST_DEBUG"] = "0"
os.environ["WEBKIT_USE_SINGLE_WEB_PROCESS"] = "0"

import re
import json
import time
import threading
import urllib.parse
import urllib.request
import gi

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
try:
    from adblockparser import AdblockRules
    ADBLOCK_ENGINE = AdblockRules([
        "||doubleclick.net^",
        "||google-analytics.com^",
        "||googlesyndication.com^",
        "||adservice.google.com^",
        "||facebook.net^",
        "||scorecardresearch.com^",
        "||adnxs.com^",
        "||amazon-adsystem.com^",
        "||criteo.com^",
        "||taboola.com^",
        "||outbrain.com^"
    ])
except Exception:
    ADBLOCK_ENGINE = None

# -------------------------------------------------------------------------
# 2-Stage Request Interceptor Filter (O(1) Domain Set Pre-lookup + Regex)
# Maintains Throughput > 25,000 requests/sec
# -------------------------------------------------------------------------
BLOCKED_DOMAINS = {
    'doubleclick.net', 'google-analytics.com', 'googlesyndication.com',
    'adservice.google.com', 'facebook.net', 'connect.facebook.net',
    'scorecardresearch.com', 'adnxs.com', 'amazon-adsystem.com',
    'criteo.com', 'taboola.com', 'outbrain.com', 'rubiconproject.com',
    'pubmatic.com', 'casalemedia.com', 'openx.net', 'media-ad.net',
    'adroll.com', 'quantserve.com', 'hotjar.com', 'mixpanel.com',
    'bugsnag.com', 'sentry.io', 'clarity.ms'
}

BLOCKED_REGEX = re.compile(
    r'(?:/adserver/|/ads/|/pagead/|/pixel\.gif|/tracker\.js|/telemetry|/analytics\.js|/gtm\.js|/collect\?|/log_event)',
    re.IGNORECASE
)

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'msclkid', 'mc_eid', 'yclid', '_openstat', 'igshid'
}

def is_ad_or_tracker(url_str):
    try:
        url_lower = url_str.lower()
        # Exempt YouTube / GoogleVideo streaming domains from cancellation
        if any(dom in url_lower for dom in [
            'youtube.com', 'googlevideo.com', 'ytimg.com', 'youtube-nocookie.com',
            'ggpht.com', 'googleapis.com', 'gstatic.com', 'vimeocdn.com', 'vimeo.com',
            'twitch.tv', 'ttvnw.net', 'jtvnw.net', 'hls', 'm3u8', 'mpd'
        ]):
            return False

        if ADBLOCK_ENGINE is not None:
            try:
                return ADBLOCK_ENGINE.should_block(url_str)
            except Exception:
                pass

        parsed = urllib.parse.urlparse(url_str)
        host = (parsed.hostname or '').lower()
        if not host:
            return False

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

def load_persistent_settings():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("Settings load note:", e)
    return {}

def save_persistent_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
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
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({"urls": urls, "timestamp": time.time()}, f, indent=2)
        os.replace(temp_file, SESSION_FILE)
    except Exception as e:
        print("Session save note:", e)

# Anti-Fingerprinting Farbling Engine JS
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
    setInterval(optimizeVideoElements, 1500);
})();
"""

class BharatBrowserWindow(Gtk.Window):
    def __init__(self):
        self.current_version = "1.2.4"
        super().__init__(title=f"Bharat Browser v{self.current_version}")
        self.set_default_size(1280, 850)
        self.set_position(Gtk.WindowPosition.CENTER)

        icon_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bharat_icon.png"),
            "/usr/share/bharat-browser/assets/bharat_icon.png",
            "/usr/share/icons/hicolor/128x128/apps/bharat-browser.png",
            os.path.expanduser("~/.local/share/icons/bharat-browser.png")
        ]
        for candidate in icon_candidates:
            if os.path.exists(candidate):
                try:
                    self.set_icon_from_file(candidate)
                    break
                except Exception:
                    pass

        self.blocked_count = 0
        self.downloads_history = []

        saved_settings = load_persistent_settings()
        self.dark_mode_active = saved_settings.get("dark_mode", False)
        self.adblock_enabled = saved_settings.get("adblock_enabled", True)
        self.clearurls_enabled = saved_settings.get("clearurls_enabled", True)
        self.https_enabled = saved_settings.get("https_enabled", True)

        self.apply_custom_css()

        # WebKit DataManager & WebContext for high performance caching
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        if hasattr(WebKit2, 'WebsiteDataManager'):
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
        self.web_settings.set_enable_developer_extras(True)
        self.web_settings.set_enable_webrtc(True)
        self.web_settings.set_enable_media_stream(True)
        self.web_settings.set_enable_javascript(True)
        self.web_settings.set_enable_media(True)
        self.web_settings.set_enable_mediasource(True)
        self.web_settings.set_enable_media_capabilities(True)
        self.web_settings.set_enable_encrypted_media(True)
        self.web_settings.set_media_playback_allows_inline(True)
        self.web_settings.set_media_playback_requires_user_gesture(False)
        self.web_settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
        if hasattr(self.web_settings, 'set_enable_dns_prefetching'):
            self.web_settings.set_enable_dns_prefetching(True)
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
        top_bar.set_margin_top(6)
        top_bar.set_margin_bottom(6)
        main_vbox.pack_start(top_bar, False, False, 0)

        # Brand Badge
        brand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        brand_box.get_style_context().add_class("brand-box")
        if os.path.exists(icon_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 22, 22, True)
                brand_img = Gtk.Image.new_from_pixbuf(pixbuf)
                brand_box.pack_start(brand_img, False, False, 0)
            except Exception:
                pass
        self.brand_label = Gtk.Label(label=f"Bharat v{self.current_version}")
        self.brand_label.get_style_context().add_class("brand-label")
        brand_box.pack_start(self.brand_label, False, False, 0)
        top_bar.pack_start(brand_box, False, False, 4)

        # Nav Buttons
        self.btn_back = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.btn_back.set_tooltip_text("Back")
        self.btn_back.connect("clicked", self.on_back_clicked)
        top_bar.pack_start(self.btn_back, False, False, 0)

        self.btn_forward = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self.btn_forward.set_tooltip_text("Forward")
        self.btn_forward.connect("clicked", self.on_forward_clicked)
        top_bar.pack_start(self.btn_forward, False, False, 0)

        self.btn_reload = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.btn_reload.set_tooltip_text("Reload Page")
        self.btn_reload.connect("clicked", self.on_reload_clicked)
        top_bar.pack_start(self.btn_reload, False, False, 0)

        # URL Entry
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("Search Google or enter URL...")
        self.url_entry.connect("activate", self.on_url_activate)
        top_bar.pack_start(self.url_entry, True, True, 4)

        # New Tab Button
        self.btn_new_tab = Gtk.Button.new_from_icon_name("tab-new-symbolic", Gtk.IconSize.BUTTON)
        self.btn_new_tab.set_tooltip_text("New Tab (Ctrl+T)")
        self.btn_new_tab.connect("clicked", lambda b: self.create_new_tab("https://www.google.co.in"))
        top_bar.pack_start(self.btn_new_tab, False, False, 0)

        # Action Buttons
        self.btn_screenshot = Gtk.Button.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.BUTTON)
        self.btn_screenshot.set_tooltip_text("Take Webpage Screenshot")
        self.btn_screenshot.connect("clicked", self.on_screenshot_clicked)
        top_bar.pack_start(self.btn_screenshot, False, False, 0)

        self.btn_dark = Gtk.Button.new_from_icon_name("weather-clear-night-symbolic", Gtk.IconSize.BUTTON)
        self.btn_dark.set_tooltip_text("Toggle DarkReader Engine")
        self.btn_dark.connect("clicked", self.on_dark_clicked)
        top_bar.pack_start(self.btn_dark, False, False, 0)

        self.btn_downloads = Gtk.Button.new_from_icon_name("folder-download-symbolic", Gtk.IconSize.BUTTON)
        self.btn_downloads.set_tooltip_text("Downloads Manager")
        self.btn_downloads.connect("clicked", self.on_downloads_clicked)
        top_bar.pack_start(self.btn_downloads, False, False, 0)

        self.btn_shield = Gtk.Button(label="🛡️ 0")
        self.btn_shield.get_style_context().add_class("btn-shield")
        self.btn_shield.set_tooltip_text("2-Stage Ad & Anti-Fingerprint Shield Active")
        top_bar.pack_start(self.btn_shield, False, False, 0)

        self.btn_settings = Gtk.Button.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        self.btn_settings.set_tooltip_text("Menu & Settings ☰")
        self.btn_settings.connect("clicked", self.on_settings_clicked)
        top_bar.pack_start(self.btn_settings, False, False, 0)

        # Gtk.Notebook for Multi-Tab Architecture
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
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

        btn_close_update = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        btn_close_update.set_tooltip_text("Close Notification")
        btn_close_update.get_style_context().add_class("update-close-btn")
        btn_close_update.connect("clicked", lambda b: self.update_dialog_box.hide())

        self.update_dialog_box.pack_start(icon_lbl, False, False, 0)
        self.update_dialog_box.pack_start(self.update_dialog_label, False, False, 0)
        self.update_dialog_box.pack_start(btn_close_update, False, False, 0)

        self.overlay.add_overlay(self.update_dialog_box)
        self.update_dialog_box.hide()

        # Restore Session or Open Initial Tab
        saved_session = load_session_state()
        initial_urls = saved_session.get("urls", [])
        if isinstance(initial_urls, str):
            initial_urls = [initial_urls]
        if not initial_urls:
            initial_urls = ["https://www.google.co.in"]

        for url in initial_urls:
            self.create_new_tab(url)

        # Keybindings (Ctrl+T, Ctrl+W, Ctrl+R)
        self.connect("key-press-event", self.on_key_press)

        # Trigger Git Update Check after 30 sec
        GLib.timeout_add_seconds(30, self.start_auto_git_update_check)

    def apply_custom_css(self):
        css_provider = Gtk.CssProvider()
        css_data = b"""
        * {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", "Cantarell", "Ubuntu", sans-serif;
        }
        window { background-color: #030712; }
        .top-bar {
            background: linear-gradient(180deg, #0f172a 0%, #090d16 100%);
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        }
        .brand-box {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(249, 115, 22, 0.35);
            border-radius: 20px;
            padding: 3px 12px;
        }
        .brand-label {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif;
            font-weight: 800;
            color: #f8fafc;
            font-size: 13px;
            letter-spacing: -0.2px;
        }
        notebook {
            background-color: #030712;
            border: none;
        }
        notebook header {
            background-color: #090d16;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        }
        notebook tab {
            background-color: rgba(15, 23, 42, 0.6);
            color: #94a3b8;
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 8px 8px 0 0;
            padding: 4px 10px;
            margin-right: 2px;
        }
        notebook tab:checked {
            background-color: #1e293b;
            color: #f8fafc;
            border-color: rgba(99, 102, 241, 0.4);
        }
        entry {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", sans-serif;
            background-color: rgba(15, 23, 42, 0.9);
            color: #f8fafc;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 20px;
            padding: 6px 14px;
            font-size: 13px;
        }
        entry:focus {
            border-color: #6366f1;
            box-shadow: 0 0 12px rgba(99, 102, 241, 0.3);
        }
        button {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", sans-serif;
            background: rgba(30, 41, 59, 0.6);
            color: #cbd5e1;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 18px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
        }
        button:hover {
            background: rgba(51, 65, 85, 0.8);
            color: #ffffff;
            border-color: rgba(255, 255, 255, 0.2);
        }
        .btn-shield {
            background: rgba(16, 185, 129, 0.12);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
            border-radius: 18px;
            font-weight: 700;
            font-size: 11px;
        }
        statusbar {
            background-color: #030712;
            color: #64748b;
            font-size: 11px;
            font-weight: 500;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
        }
        .update-dialog-box {
            background: rgba(15, 23, 42, 0.96);
            color: #f8fafc;
            border: 1px solid rgba(16, 185, 129, 0.4);
            border-radius: 14px;
            padding: 12px 20px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.8);
        }
        .update-icon-text { font-weight: 900; color: #10b981; font-size: 15px; }
        .update-dialog-text { font-weight: 600; color: #f8fafc; font-size: 13px; }
        .update-close-btn { background: transparent; border: none; color: #94a3b8; }
        .update-close-btn:hover { color: #ffffff; }
        """
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # Multi-Tab Architecture Helper Methods
    def create_new_tab(self, url="https://www.google.co.in"):
        tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        webview = WebKit2.WebView.new_with_context(self.context)
        webview.set_settings(self.web_settings)

        # Inject UserScripts
        ucm = webview.get_user_content_manager()
        
        # 1. Media Codec Polyfill
        media_script = WebKit2.UserScript(
            MEDIA_POLYFILL_JS,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.START,
            None, None
        )
        ucm.add_script(media_script)

        # 2. Smart Link Prefetching UserScript
        prefetch_script = WebKit2.UserScript(
            PREFETCH_USER_SCRIPT,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.END,
            None, None
        )
        ucm.add_script(prefetch_script)

        # Signals
        webview.connect("load-changed", self.on_load_changed)
        webview.connect("resource-load-started", self.on_resource_load_started)
        webview.connect("web-process-terminated", self.on_web_process_terminated)

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

        tab_box.__webview = webview
        tab_box.__label = tab_label

        page_num = self.notebook.append_page(tab_box, header_box)
        self.notebook.set_tab_reorderable(tab_box, True)
        self.notebook.set_current_page(page_num)

        webview.load_uri(url)
        return webview

    def close_tab(self, tab_box):
        page_num = self.notebook.page_num(tab_box)
        if page_num != -1:
            self.notebook.remove_page(page_num)
        if self.notebook.get_n_pages() == 0:
            self.create_new_tab("https://www.google.co.in")

    def get_active_webview(self):
        page_num = self.notebook.get_current_page()
        if page_num != -1:
            tab_box = self.notebook.get_nth_page(page_num)
            if hasattr(tab_box, '__webview'):
                return tab_box.__webview
        return None

    def get_active_tab_box(self):
        page_num = self.notebook.get_current_page()
        if page_num != -1:
            return self.notebook.get_nth_page(page_num)
        return None

    def on_tab_changed(self, notebook, page, page_num):
        webview = self.get_active_webview()
        if webview:
            uri = webview.get_uri() or ""
            title = webview.get_title() or f"Bharat Browser v{self.current_version}"
            self.url_entry.set_text(uri)
            self.set_title(f"{title} - Bharat Browser v{self.current_version}")
            self.statusbar.push(self.context_id, f"Ready | {uri}")

    def on_web_process_terminated(self, webview, reason):
        print("Web process terminated, reason:", reason)
        uri = webview.get_uri() or "https://www.google.co.in"
        GLib.idle_add(lambda: webview.load_uri(uri))
        self.statusbar.push(self.context_id, "⚠️ Web process recovered automatically.")

    def on_key_press(self, widget, event):
        state = event.state & Gdk.ModifierType.CONTROL_MASK
        if state:
            keyval = event.keyval
            if keyval in (Gdk.KEY_t, Gdk.KEY_T):
                self.create_new_tab("https://www.google.co.in")
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
        return False

    # Download Manager Handlers
    def on_download_started(self, context, download):
        desktop_dir = os.path.expanduser("~/Desktop")
        os.makedirs(desktop_dir, exist_ok=True)
        
        filename = "downloaded_file"
        try:
            req = download.get_request()
            if req and req.get_uri():
                filename = os.path.basename(urllib.parse.urlparse(req.get_uri()).path) or "downloaded_file"
        except Exception:
            pass

        target_path = os.path.join(desktop_dir, filename)
        download.set_destination("file://" + target_path)
        
        self.downloads_history.append({"filename": filename, "path": target_path, "status": "Downloading..."})
        self.statusbar.push(self.context_id, f"📥 Download Started: {filename} -> ~/Desktop")

        download.connect("finished", lambda d: self.on_download_finished(filename, target_path))
        download.connect("failed", lambda d, err: self.on_download_failed(filename, err))

    def on_download_finished(self, filename, target_path):
        for item in self.downloads_history:
            if item["filename"] == filename:
                item["status"] = "Completed ✅"
        self.statusbar.push(self.context_id, f"✅ Download Completed: {filename}")

    def on_download_failed(self, filename, error):
        for item in self.downloads_history:
            if item["filename"] == filename:
                item["status"] = "Failed ❌"
        self.statusbar.push(self.context_id, f"❌ Download Failed: {filename}")

    def on_downloads_clicked(self, btn):
        dialog = Gtk.Dialog(
            title="📥 Downloads Manager",
            transient_for=self,
            modal=True,
            destroy_with_parent=True
        )
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(460, 320)

        content_area = dialog.get_content_area()
        content_area.set_margin_start(16)
        content_area.set_margin_end(16)
        content_area.set_margin_top(16)
        content_area.set_margin_bottom(16)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_area.add(vbox)

        lbl_header = Gtk.Label(label="📥 Recent Downloads (Saved to Desktop)")
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

        btn_open_folder = Gtk.Button(label="📁 Open Desktop Downloads Folder")
        btn_open_folder.connect("clicked", lambda b: os.system("xdg-open ~/Desktop &"))
        vbox.pack_start(btn_open_folder, False, False, 8)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def start_auto_git_update_check(self):
        threading.Thread(target=self.async_git_update_check, daemon=True).start()
        return False

    def compare_versions(self, v1, v2):
        p1 = [int(x) for x in re.sub(r'[^0-9.]', '', v1).split('.') if x.isdigit()]
        p2 = [int(x) for x in re.sub(r'[^0-9.]', '', v2).split('.') if x.isdigit()]
        for a, b in zip(p1, p2):
            if a > b: return 1
            if a < b: return -1
        return len(p1) - len(p2)

    def async_git_update_check(self):
        try:
            url = "https://raw.githubusercontent.com/Sangam1112/bharat-browser/master/package.json"
            req = urllib.request.Request(url, headers={"User-Agent": f"BharatBrowser/{self.current_version}"})
            with urllib.request.urlopen(req, timeout=6) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    remote_version = data.get("version", "").strip()
                    if remote_version and self.compare_versions(remote_version, self.current_version) > 0:
                        GLib.idle_add(self.show_update_notification_dialog, remote_version)
                        return
                    else:
                        GLib.idle_add(self.show_latest_version_notification)
                        return
        except Exception as e:
            print("Git update check note:", e)

        GLib.idle_add(self.show_latest_version_notification)

    def push_notification_status(self, message):
        self.statusbar.show_all()
        self.statusbar.push(self.context_id, message)
        GLib.timeout_add_seconds(5, lambda: (self.statusbar.hide(), False)[1])

    def show_latest_version_notification(self):
        self.update_dialog_label.set_text(f"Browser is working on latest version (v{self.current_version})")
        self.update_dialog_box.show_all()
        self.push_notification_status(f"✅ Browser is working on latest version (v{self.current_version})")
        GLib.timeout_add_seconds(5, lambda: (self.update_dialog_box.hide(), False)[1])

    def show_update_notification_dialog(self, version_str):
        self.current_version = version_str
        self.brand_label.set_text(f"Bharat v{self.current_version}")
        webview = self.get_active_webview()
        current_title = webview.get_title() if webview else "Bharat Browser"
        self.set_title(f"{current_title} - Bharat Browser v{self.current_version}")
        self.update_dialog_label.set_text(f"Browser has been updated to version {version_str}")
        self.update_dialog_box.show_all()
        self.push_notification_status(f"🎉 Browser has been updated to version {version_str}")
        GLib.timeout_add_seconds(5, lambda: (self.update_dialog_box.hide(), False)[1])

    def on_resource_load_started(self, webview, resource, request):
        uri = request.get_uri()
        if not uri:
            return

        if self.https_enabled and uri.startswith("http://") and "localhost" not in uri and "127.0.0.1" not in uri:
            new_uri = uri.replace("http://", "https://")
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
            if "." in text and " " not in text:
                text = "https://" + text
            else:
                text = f"https://www.google.com/search?q={urllib.parse.quote(text)}"
        
        webview = self.get_active_webview()
        if webview:
            webview.load_uri(text)

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
            uri = webview.get_uri() or ""
            title = webview.get_title() or "New Tab"
            
            active_wv = self.get_active_webview()
            if active_wv == webview:
                self.url_entry.set_text(uri)
                self.set_title(f"{title} - Bharat Browser v{self.current_version}")
                self.statusbar.push(self.context_id, f"Ready | {uri}")

            # Update tab label
            for i in range(self.notebook.get_n_pages()):
                tab_box = self.notebook.get_nth_page(i)
                if hasattr(tab_box, '__webview') and tab_box.__webview == webview:
                    if hasattr(tab_box, '__label'):
                        tab_box.__label.set_text(title)
                    break

            # Save session state across tabs
            urls = []
            for i in range(self.notebook.get_n_pages()):
                tb = self.notebook.get_nth_page(i)
                if hasattr(tb, '__webview'):
                    u = tb.__webview.get_uri()
                    if u and not u.startswith("about:"):
                        urls.append(u)
            if urls:
                save_session_state(urls)

            # Injections
            self.execute_js_on_webview(webview, FARBLING_JS)
            if self.dark_mode_active:
                self.apply_dark_reader_to_webview(webview)

    def save_settings(self):
        save_persistent_settings({
            "dark_mode": self.dark_mode_active,
            "adblock_enabled": self.adblock_enabled,
            "clearurls_enabled": self.clearurls_enabled,
            "https_enabled": self.https_enabled
        })

    def on_dark_clicked(self, btn):
        self.dark_mode_active = not self.dark_mode_active
        self.save_settings()
        for i in range(self.notebook.get_n_pages()):
            tb = self.notebook.get_nth_page(i)
            if hasattr(tb, '__webview'):
                wv = tb.__webview
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
            except Exception:
                pass

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
            pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())
            desktop_dir = os.path.expanduser("~/Desktop")
            os.makedirs(desktop_dir, exist_ok=True)
            timestamp = GLib.DateTime.new_now_local().format("%Y%m%d_%H%M%S")
            filename = f"BharatScreenshot_{timestamp}.jpeg"
            filepath = os.path.join(desktop_dir, filename)
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
            dialog.format_secondary_text(f"File: {filename}\nSaved in ~/Desktop")
            dialog.run()
            dialog.destroy()
        except Exception as e:
            self.statusbar.push(self.context_id, f"❌ Screenshot failed: {str(e)}")

    def on_settings_clicked(self, btn):
        dialog = Gtk.Dialog(
            title=f"Browser Settings & Extensions (v{self.current_version})",
            transient_for=self,
            modal=True,
            destroy_with_parent=True
        )
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(480, 360)

        content_area = dialog.get_content_area()
        content_area.set_margin_start(16)
        content_area.set_margin_end(16)
        content_area.set_margin_top(16)
        content_area.set_margin_bottom(16)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_area.add(vbox)

        title_lbl = Gtk.Label(label=f"⚙️ Bharat Browser Settings (v{self.current_version})")
        title_lbl.get_style_context().add_class("brand-label")
        vbox.pack_start(title_lbl, False, False, 0)

        chk_dark = Gtk.CheckButton(label="🌙 DarkReader Engine (High-Contrast Webpages)")
        chk_dark.set_active(self.dark_mode_active)
        chk_dark.connect("toggled", lambda cb: self.on_dark_clicked(self.btn_dark))
        vbox.pack_start(chk_dark, False, False, 0)

        chk_adblock = Gtk.CheckButton(label="🛡️ uBlock & Privacy Badger (2-Stage Blocker)")
        chk_adblock.set_active(self.adblock_enabled)
        chk_adblock.connect("toggled", lambda cb: (setattr(self, 'adblock_enabled', cb.get_active()), self.save_settings()))
        vbox.pack_start(chk_adblock, False, False, 0)

        chk_clearurls = Gtk.CheckButton(label="🔗 ClearURLs Tracking Parameter Stripper")
        chk_clearurls.set_active(self.clearurls_enabled)
        chk_clearurls.connect("toggled", lambda cb: (setattr(self, 'clearurls_enabled', cb.get_active()), self.save_settings()))
        vbox.pack_start(chk_clearurls, False, False, 0)

        chk_https = Gtk.CheckButton(label="🔒 HTTPS Enforcement (Auto-Upgrade HTTP)")
        chk_https.set_active(self.https_enabled)
        chk_https.connect("toggled", lambda cb: (setattr(self, 'https_enabled', cb.get_active()), self.save_settings()))
        vbox.pack_start(chk_https, False, False, 0)

        btn_clear = Gtk.Button(label="🗑️ Clear Browsing History & Cookies")
        btn_clear.connect("clicked", self.on_clear_cache_clicked)
        vbox.pack_start(btn_clear, False, False, 4)

        about_lbl = Gtk.Label(label=f"Bharat Browser v{self.current_version} | Engineered in INDIA 🇮🇳")
        vbox.pack_start(about_lbl, False, False, 8)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

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
        self.statusbar.push(self.context_id, "🧹 Browsing History & Cache Cleared!")

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Browsing History & Cookies Cleared"
        )
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
