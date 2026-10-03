#!/usr/bin/env python3
"""
Bharat Browser v1.4.0 - GTK3 / WebKit2 Python Application
Modern, Ultra-Fast, Multi-Tab, and Privacy-First Web Browser engineered for Linux (Ubuntu)
"""
import sys
import os
import json
import shutil

APP_VERSION = "1.4.0"
# The self-updater cannot rewrite a root-owned package install, so it keeps its updates in a per-user copy
# that the launcher (/usr/bin/bharat-browser) prefers over the system one.
USER_INSTALL_DIR = os.path.expanduser("~/.local/share/bharat-browser")
SYSTEM_SCRIPT = "/usr/share/bharat-browser/bharat_browser.py"


def _version_tuple(v):
    import re
    out = []
    for token in re.split(r"[.\-+]", str(v)):
        m = re.match(r"\d+", token)
        if not m:
            break
        out.append(int(m.group()))
    return tuple(out)


def _prefer_newest_copy():
    """If this is the per-user updated copy and the system package has since been upgraded past it, drop this
    stale copy and start the system one, so an old per-user copy can never shadow a newer package."""
    import re
    here = os.path.abspath(__file__)
    if os.path.dirname(here) != USER_INSTALL_DIR or not os.path.isfile(SYSTEM_SCRIPT):
        return
    try:
        with open(SYSTEM_SCRIPT, encoding="utf-8") as f:
            m = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M)
        if m and _version_tuple(m.group(1)) > _version_tuple(APP_VERSION):
            os.remove(here)
            os.execv(sys.executable, [sys.executable, SYSTEM_SCRIPT] + sys.argv[1:])
    except Exception:
        pass


_prefer_newest_copy()

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
import glob
import hashlib
import html as html_module
import ipaddress
import re
import secrets as secrets_module
import sqlite3
import tempfile
import time
import threading
import subprocess
import urllib.parse
import urllib.request
from collections import Counter
import gi
import cairo

gi.require_version('Gtk', '3.0')
try:
    gi.require_version('WebKit2', '4.1')
except ValueError:
    gi.require_version('WebKit2', '4.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GObject, WebKit2, GLib, Gio, Pango

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
    if not host:
        return False
    if host in domain_set:
        return True
    parts = host.split('.')
    for i in range(1, len(parts)):
        parent = '.'.join(parts[i:])
        if parent in domain_set:
            return True
    return False

def is_ad_or_tracker(url_str):
    try:
        if url_str.startswith("https://"):
            rest = url_str[8:]
        elif url_str.startswith("http://"):
            rest = url_str[7:]
        else:
            parsed = urllib.parse.urlparse(url_str)
            host = (parsed.hostname or '').lower()
            path = parsed.path
            rest = None

        if rest is not None:
            slash_idx = rest.find('/')
            q_idx = rest.find('?')
            if slash_idx != -1 and (q_idx == -1 or slash_idx < q_idx):
                host = rest[:slash_idx]
                path = rest[slash_idx:q_idx] if q_idx != -1 else rest[slash_idx:]
            elif q_idx != -1:
                host = rest[:q_idx]
                path = ""
            else:
                host = rest
                path = ""
            if ':' in host:
                host = host.split(':', 1)[0]
            host = host.lower()

        if not host:
            return False

        # Exempt YouTube / GoogleVideo streaming domains and manifest files from cancellation
        if _host_matches_domain_set(host, STREAMING_EXEMPT_DOMAINS):
            return False
        if path.lower().endswith(STREAMING_EXEMPT_EXTENSIONS):
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
        # Stage 1: O(1) Hierarchical Domain Set Pre-lookup
        if _host_matches_domain_set(host, BLOCKED_DOMAINS):
            return True

        # Stage 2: Path Regex Matching
        if path and BLOCKED_REGEX.search(path):
            return True
    except Exception:
        pass
    return False

def build_content_blocker_rules_json(extra_domains=()):
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
    # Domains from the downloaded tracker list are limited to third-party loads, so
    # visiting a listed site directly never gets blocked.
    for domain in sorted(set(extra_domains) - set(BLOCKED_DOMAINS)):
        # One rule per domain (WebKit always gives http(s) URLs a "/" path), which
        # keeps the rule count, and so memory use, as low as possible.
        rules.append({"trigger": {"url-filter": f"^https?://([a-z0-9-]+\\.)*{re.escape(domain)}[:/]",
                                  "load-type": ["third-party"]},
                      "action": {"type": "block"}})
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

# --- Update signature verification (Ed25519, RFC 8032) -----------------------
# The updater only installs a release whose package.json "signature" verifies
# against this public key; the matching private key lives off-repo on the
# release machine (tools/sign-release.py). Verification is pure Python so end
# users need no extra packages. Signed message:
#   b"bharat-browser-update\n" + version + b"\n" + bharat_browser.py bytes
# NOTE: changing the key means users on older versions can no longer
# auto-update; they must reinstall the package once.
UPDATE_PUBLIC_KEY_HEX = "5c846fe6ac0d47c188f3f71ade52566f75a3f9af80764c5064a1a29859ec7844"
_UPDATE_SIGNATURE_PREFIX = b"bharat-browser-update\n"

# ed25519 verify begin
_ED_P = 2 ** 255 - 19
_ED_L = 2 ** 252 + 27742317777372353535851937790883648493
_ED_D = -121665 * pow(121666, _ED_P - 2, _ED_P) % _ED_P
_ED_I = pow(2, (_ED_P - 1) // 4, _ED_P)


def _ed_recover_x(y, sign):
    if y >= _ED_P:
        return None
    x2 = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_P - 2, _ED_P) % _ED_P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_ED_P + 3) // 8, _ED_P)
    if (x * x - x2) % _ED_P != 0:
        x = x * _ED_I % _ED_P
    if (x * x - x2) % _ED_P != 0:
        return None
    if (x & 1) != sign:
        x = _ED_P - x
    return x


def _ed_add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _ED_P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _ED_P
    C = 2 * a[3] * b[3] * _ED_D % _ED_P
    D = 2 * a[2] * b[2] % _ED_P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _ED_P, G * H % _ED_P, F * G % _ED_P, E * H % _ED_P)


def _ed_mul(k, point):
    result = (0, 1, 1, 0)
    while k > 0:
        if k & 1:
            result = _ed_add(result, point)
        point = _ed_add(point, point)
        k >>= 1
    return result


def _ed_decompress(data):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _ed_recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _ED_P)


_ED_BASE_Y = 4 * pow(5, _ED_P - 2, _ED_P) % _ED_P
_ED_BASE_X = _ed_recover_x(_ED_BASE_Y, 0)
_ED_BASE = (_ED_BASE_X, _ED_BASE_Y, 1, _ED_BASE_X * _ED_BASE_Y % _ED_P)


def ed25519_verify(public_key, message, signature):
    """True only if `signature` is a valid Ed25519 signature of `message`."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    A = _ed_decompress(public_key)
    R = _ed_decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if A is None or R is None or s >= _ED_L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _ED_L
    lhs = _ed_mul(s, _ED_BASE)
    rhs = _ed_add(R, _ed_mul(h, A))
    return ((lhs[0] * rhs[2] - rhs[0] * lhs[2]) % _ED_P == 0
            and (lhs[1] * rhs[2] - rhs[1] * lhs[2]) % _ED_P == 0)
# ed25519 verify end


def verify_update_signature(version, source, signature_hex):
    """True if `source` (bharat_browser.py bytes) is the release signed for `version`."""
    try:
        signature = bytes.fromhex(signature_hex)
        message = _UPDATE_SIGNATURE_PREFIX + version.encode() + b"\n" + source
        return ed25519_verify(bytes.fromhex(UPDATE_PUBLIC_KEY_HEX), message, signature)
    except (ValueError, TypeError):
        return False


# Only errors that mean the machine has no usable network. Per-site problems
# ("name or service not known" for a mistyped domain, connection refused, a slow
# server timing out) are deliberately not listed: they'd wrongly blame the user's
# internet.
_OFFLINE_ERROR_HINTS = (
    "temporary failure in name resolution", "network is unreachable", "no route to host",
)


def looks_offline(error_text):
    """True if an error message (WebKit/GLib/urllib) means 'no usable network', as
    opposed to a problem with one particular site. Also true whenever the OS
    itself reports no network at all."""
    text = (error_text or "").lower()
    if any(hint in text for hint in _OFFLINE_ERROR_HINTS):
        return True
    try:
        return not Gio.NetworkMonitor.get_default().get_network_available()
    except Exception:
        return False


_ERROR_PAGE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 50% -10%,#16213a 0,#0b0e14 60%);
 color:#f8fafc;font-family:system-ui,sans-serif;display:flex;justify-content:center;padding:0 20px}
.flag{position:fixed;top:0;left:0;right:0;height:5px;background:linear-gradient(90deg,#ff9933 33%,#fff 33% 66%,#138808 66%)}
.wrap{max-width:560px;width:100%;padding:56px 0 40px;text-align:center}
.sig{position:relative;width:84px;height:84px;margin:0 auto 18px;display:flex;align-items:center;justify-content:center;font-size:42px}
.sig i{position:absolute;inset:0;border:2px solid #ff9933;border-radius:50%;opacity:0;animation:p 2.4s infinite}
.sig i:nth-child(2){animation-delay:.8s}.sig i:nth-child(3){animation-delay:1.6s}
@keyframes p{0%{transform:scale(.4);opacity:.9}100%{transform:scale(1.25);opacity:0}}
h1{font-size:28px;margin:0 0 10px}
p{color:#cbd5e1;line-height:1.6;margin:8px 0}
ul{text-align:left;display:inline-block;color:#cbd5e1;line-height:1.8;margin:4px 0}
.btn{display:inline-block;margin:14px 0 4px;background:#2563eb;color:#fff;padding:11px 26px;border-radius:999px;
 text-decoration:none;font-weight:600;box-shadow:0 4px 18px #2563eb55;transition:transform .15s}
.btn:hover{transform:translateY(-2px)}
.btn.alt{background:transparent;border:1px solid #475569;box-shadow:none;color:#cbd5e1;margin-left:8px}
.live{color:#4ade80;font-size:13px;margin-top:6px}
.game{margin:26px auto 6px;border:1px solid #243049;border-radius:14px;overflow:hidden;background:#0f1626}
canvas{display:block;width:100%;height:auto;cursor:pointer}
.hint{color:#94a3b8;font-size:12px;margin:6px 0 18px}
.tech{color:#64748b;font-size:12px;margin-top:22px;word-break:break-word}
</style></head>
<body data-uri="@URI@"><div class="flag"></div><div class="wrap">
<div class="sig">@ICON@@PULSE@</div>
<h1>@HEADING@</h1>
@BODY@
@ACTIONS@
@LIVE@
@GAME@
<div class="tech">Technical details: @TECH@</div>
</div>
<script>
var U=document.body.dataset.uri;
@SCRIPT@
</script></body></html>"""

_OFFLINE_PAGE_SCRIPT = r"""
var was=navigator.onLine;
function back(){location.href=U}
window.addEventListener('online',back);
setInterval(function(){var n=navigator.onLine;if(n&&!was)back();was=n},3000);
(function(){
var c=document.getElementById('g'),x=c.getContext('2d'),W=c.width,H=c.height,best=0;
var k,vy,birds,score,t,state;
function reset(){k=H/2;vy=0;birds=[];score=0;t=0;state='play'}
function flap(e){if(e)e.preventDefault();if(state=='idle'||state=='over')reset();vy=-5.4}
c.addEventListener('mousedown',flap);c.addEventListener('touchstart',flap);
document.addEventListener('keydown',function(e){if(e.code=='Space'||e.code=='ArrowUp')flap(e)});
function bird(b){x.strokeStyle='#cbd5e1';x.lineWidth=2;x.beginPath();var f=Math.sin(t/4+b.p)*5;
 x.moveTo(b.x-10,b.y+f);x.quadraticCurveTo(b.x-4,b.y-6,b.x,b.y);x.quadraticCurveTo(b.x+4,b.y-6,b.x+10,b.y+f);x.stroke()}
function draw(){
 var g=x.createLinearGradient(0,0,0,H);g.addColorStop(0,'#1e2a4a');g.addColorStop(1,'#f9731633');
 x.fillStyle=g;x.fillRect(0,0,W,H);
 x.strokeStyle='#64748b';x.lineWidth=1;x.beginPath();x.moveTo(60,H);x.lineTo(90,k+16);x.stroke();
 x.save();x.translate(90,k);x.rotate(Math.max(-.5,Math.min(.6,vy/10)));
 x.fillStyle='#ff9933';x.beginPath();x.moveTo(0,-16);x.lineTo(13,0);x.lineTo(0,16);x.lineTo(-13,0);x.closePath();x.fill();
 x.fillStyle='#138808';x.beginPath();x.moveTo(0,-16);x.lineTo(13,0);x.lineTo(0,0);x.closePath();x.fill();
 x.fillStyle='#fff';x.beginPath();x.moveTo(-13,0);x.lineTo(0,0);x.lineTo(0,16);x.closePath();x.fill();
 x.strokeStyle='#fbbf24';x.beginPath();x.moveTo(0,16);for(var i=1;i<=4;i++)x.lineTo(Math.sin(t/5+i)*6,16+i*7);x.stroke();x.restore();
 birds.forEach(bird);
 x.fillStyle='#e2e8f0';x.font='14px system-ui';x.textAlign='left';x.fillText('Score '+score+'   Best '+best,10,20);
 x.textAlign='center';x.font='16px system-ui';
 if(state=='idle')x.fillText('Click or press Space to fly the kite',W/2,H/2-30);
 if(state=='over')x.fillText('Cut! Click or press Space to fly again',W/2,H/2-30);
}
function step(){
 if(state=='play'){t++;vy+=.3;k+=vy;
  if(t%70==0)birds.push({x:W+10,y:30+Math.random()*(H-60),p:Math.random()*6,d:0});
  birds.forEach(function(b){b.x-=2.6+score*.08;if(!b.d&&b.x<80){b.d=1;score++;best=Math.max(best,score)}
   if(Math.abs(b.x-90)<18&&Math.abs(b.y-k)<18)state='over'});
  birds=birds.filter(function(b){return b.x>-20});
  if(k<8||k>H-8)state='over';}
 draw();requestAnimationFrame(step)}
reset();state='idle';step();
})();
"""


def build_error_page(host, uri, error_message, offline):
    """HTML for the 'page didn't load' screen. All page-supplied text is escaped."""
    esc = GLib.markup_escape_text
    page = _ERROR_PAGE_TEMPLATE
    if offline:
        parts = {
            "ICON": "📡", "PULSE": "<i></i><i></i><i></i>",
            "HEADING": "No internet connection",
            "BODY": (f"<p>Bharat Browser couldn't reach <b>{esc(host)}</b> because your computer "
                     "isn't connected to the internet.</p>"
                     "<ul><li>Check that Wi-Fi or your network cable is connected</li>"
                     "<li>Restart your router if other apps are offline too</li></ul>"),
            "LIVE": "<div class='live'>🔄 This page will reload by itself when you're back online</div>",
            "GAME": ("<div class='game'><canvas id='g' width='560' height='220'></canvas></div>"
                     "<div class='hint'>While you wait: fly the kite 🪁 and dodge the birds</div>"),
            "SCRIPT": _OFFLINE_PAGE_SCRIPT,
        }
    else:
        parts = {
            "ICON": "⚠️", "PULSE": "", "HEADING": "This page didn't load",
            "BODY": f"<p>Bharat Browser couldn't reach <b>{esc(host)}</b>. The site may be down or the address may be wrong.</p>",
            "LIVE": "", "GAME": "", "SCRIPT": "",
        }
    parts["TECH"] = esc(error_message)
    parts["URI"] = esc(uri).replace('"', "&quot;")
    parts["ACTIONS"] = f'<a class="btn" href="{parts["URI"]}">Try again</a>'
    return _fill_error_template(page, parts)


def _fill_error_template(page, parts):
    """Single-pass @KEY@ substitution: text inside a substituted value (an error
    message that happens to contain "@HEADING@", say) is never substituted again."""
    return re.sub(r"@([A-Z]+)@", lambda m: parts.get(m.group(1), m.group(0)), page)


def build_notice_page(icon, heading, body_html, actions_html, tech=""):
    """Same look as the error page, for other notices (crashes, HTTP warnings).
    `body_html` and `actions_html` are inserted as-is, so callers must escape any
    page-controlled text in them."""
    return _fill_error_template(_ERROR_PAGE_TEMPLATE, {
        "ICON": icon, "PULSE": "", "HEADING": heading, "BODY": body_html, "ACTIONS": actions_html,
        "LIVE": "", "GAME": "", "SCRIPT": "", "URI": "", "TECH": GLib.markup_escape_text(tech)})


# Chrome-compatible UA so sites don't serve "unsupported browser" pages or flag
# an outdated client as a bot. Chrome freezes everything after the major
# version to ".0.0.0", so only CHROME_UA_MAJOR needs bumping. Update it each
# release to the current stable major (Chrome 154 as of Oct 2026).
CHROME_UA_MAJOR = 154
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_UA_MAJOR}.0.0.0 Safari/537.36"
)

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

def save_session_state(urls, pinned=()):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temp_file = SESSION_FILE + ".tmp"
        _write_json_private(temp_file, {"urls": urls, "pinned": list(pinned), "timestamp": time.time()}, indent=None)
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

# ---------------------------------------------------------------------------
# Import bookmarks / history from other browsers
# ---------------------------------------------------------------------------
_CHROMIUM_FAMILY_DIRS = {
    "Google Chrome": ("~/.config/google-chrome", "~/.var/app/com.google.Chrome/config/google-chrome"),
    "Chromium": ("~/.config/chromium", "~/snap/chromium/common/chromium", "~/.var/app/org.chromium.Chromium/config/chromium"),
    "Brave": ("~/.config/BraveSoftware/Brave-Browser", "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"),
    "Microsoft Edge": ("~/.config/microsoft-edge",),
    "Vivaldi": ("~/.config/vivaldi",),
    "Opera": ("~/.config/opera",),
}
_FIREFOX_DIRS = ("~/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox", "~/.var/app/org.mozilla.firefox/.mozilla/firefox")


def find_importable_profiles(home=None):
    """Browser profiles on this machine that bookmarks/history can be read from.
    Returns dicts {"browser", "profile", "kind" ("firefox"|"chromium"), "path"}."""
    expand = (lambda p: p.replace("~", home, 1)) if home else os.path.expanduser
    found = []
    for base in _FIREFOX_DIRS:
        for places in sorted(glob.glob(os.path.join(expand(base), "*", "places.sqlite"))):
            profile_dir = os.path.dirname(places)
            found.append({"browser": "Firefox", "profile": os.path.basename(profile_dir),
                          "kind": "firefox", "path": profile_dir})
    for name, bases in _CHROMIUM_FAMILY_DIRS.items():
        for base in bases:
            base = expand(base)
            for profile_dir in sorted(glob.glob(os.path.join(base, "Default")) + glob.glob(os.path.join(base, "Profile *"))):
                if os.path.exists(os.path.join(profile_dir, "Bookmarks")) or os.path.exists(os.path.join(profile_dir, "History")):
                    found.append({"browser": name, "profile": os.path.basename(profile_dir),
                                  "kind": "chromium", "path": profile_dir})
    return found


def _chromium_time_to_epoch(value):
    """Chromium timestamps are microseconds since 1601-01-01."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return time.time()
    return v / 1_000_000 - 11644473600 if v > 0 else time.time()


def _is_web_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _query_copied_sqlite(db_path, query, params=()):
    """Run a read-only query on a COPY of a browser's database: the original is
    usually locked (or mid-write) while that browser is running."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, "db.sqlite")
        shutil.copy2(db_path, dst)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                shutil.copy2(db_path + suffix, dst + suffix)
        conn = sqlite3.connect(dst)
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()


def read_chromium_bookmarks(profile_dir):
    path = os.path.join(profile_dir, "Bookmarks")
    out = []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "url" and _is_web_url(node.get("url")):
            out.append({"url": node["url"], "title": (node.get("name") or node["url"]).strip(),
                        "added": _chromium_time_to_epoch(node.get("date_added"))})
        for child in node.get("children") or []:
            walk(child)

    for root in (data.get("roots") or {}).values():
        walk(root)
    return out


def read_chromium_history(profile_dir, limit=HISTORY_MAX_ENTRIES):
    rows = _query_copied_sqlite(
        os.path.join(profile_dir, "History"),
        "SELECT url, title, visit_count, last_visit_time FROM urls "
        "WHERE url LIKE 'http%' ORDER BY last_visit_time DESC LIMIT ?", (limit,))
    return [{"url": u, "title": (t or u).strip(), "total_seconds": 0.0, "visits": max(int(c or 1), 1),
             "last_visited": _chromium_time_to_epoch(ts)} for u, t, c, ts in rows if _is_web_url(u)]


def read_firefox_bookmarks(profile_dir):
    rows = _query_copied_sqlite(
        os.path.join(profile_dir, "places.sqlite"),
        "SELECT b.title, p.url, b.dateAdded FROM moz_bookmarks b JOIN moz_places p ON b.fk = p.id "
        "WHERE b.type = 1 AND p.url LIKE 'http%'")
    return [{"url": u, "title": (t or u).strip(), "added": (a / 1_000_000) if a else time.time()}
            for t, u, a in rows if _is_web_url(u)]


def read_firefox_history(profile_dir, limit=HISTORY_MAX_ENTRIES):
    rows = _query_copied_sqlite(
        os.path.join(profile_dir, "places.sqlite"),
        "SELECT url, title, visit_count, last_visit_date FROM moz_places "
        "WHERE url LIKE 'http%' AND visit_count > 0 AND last_visit_date IS NOT NULL "
        "ORDER BY last_visit_date DESC LIMIT ?", (limit,))
    return [{"url": u, "title": (t or u).strip(), "total_seconds": 0.0, "visits": max(int(c or 1), 1),
             "last_visited": ts / 1_000_000} for u, t, c, ts in rows if _is_web_url(u)]


def read_profile(profile, want_bookmarks=True, want_history=True):
    """Returns (bookmarks, history, errors) for one profile; a failure in one
    part (e.g. unreadable database) doesn't lose the other."""
    bookmarks, history, errors = [], [], []
    firefox = profile["kind"] == "firefox"
    if want_bookmarks:
        try:
            bookmarks = (read_firefox_bookmarks if firefox else read_chromium_bookmarks)(profile["path"])
        except Exception as e:
            errors.append(f"bookmarks: {e}")
    if want_history:
        try:
            history = (read_firefox_history if firefox else read_chromium_history)(profile["path"])
        except Exception as e:
            errors.append(f"history: {e}")
    return bookmarks, history, errors


def merge_bookmarks(existing, incoming, limit=BOOKMARKS_MAX_ENTRIES):
    """Existing bookmarks win; returns (merged, number_added)."""
    seen = {b.get("url") for b in existing}
    merged, added = list(existing), 0
    for b in incoming:
        if b["url"] in seen or len(merged) >= limit:
            continue
        seen.add(b["url"])
        merged.append(b)
        added += 1
    return merged, added


def merge_history(existing, incoming, limit=HISTORY_MAX_ENTRIES):
    """Existing entries win; result is most-recent-first and capped. Returns (merged, number_added)."""
    existing_urls = {e.get("url") for e in existing}
    by_url = {e["url"]: e for e in incoming}
    by_url.update({e.get("url"): e for e in existing})
    merged = sorted(by_url.values(), key=lambda e: e.get("last_visited", 0), reverse=True)[:limit]
    return merged, sum(1 for e in merged if e.get("url") not in existing_urls)


def parse_netscape_bookmarks(text):
    """Bookmarks from the HTML export every browser can produce (Netscape format)."""
    out = []
    for m in re.finditer(r"<A\s([^>]*)>(.*?)</A>", text, re.I | re.S):
        attrs, title = m.groups()
        href = re.search(r'HREF="([^"]*)"', attrs, re.I)
        if not href:
            continue
        url = html_module.unescape(href.group(1)).strip()
        if not _is_web_url(url):
            continue
        added = re.search(r'ADD_DATE="(\d+)"', attrs, re.I)
        title = html_module.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        out.append({"url": url, "title": title or url, "added": int(added.group(1)) if added else time.time()})
    return out


# ---------------------------------------------------------------------------
# Per-site settings (permissions, zoom, ad blocking, JavaScript)
# ---------------------------------------------------------------------------
SITE_SETTINGS_FILE = os.path.join(CONFIG_DIR, "site_settings.json")
PERMISSION_KINDS = {"media": "Camera & microphone", "location": "Location", "notifications": "Notifications"}


def load_site_settings():
    try:
        if os.path.exists(SITE_SETTINGS_FILE):
            with open(SITE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {h: v for h, v in data.items() if isinstance(h, str) and isinstance(v, dict)}
    except Exception as e:
        print("Site settings load note:", e)
    return {}


def save_site_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temp_file = SITE_SETTINGS_FILE + ".tmp"
        _write_json_private(temp_file, settings)
        os.replace(temp_file, SITE_SETTINGS_FILE)
    except Exception as e:
        print("Site settings save note:", e)


def site_host_of(uri):
    try:
        return (urllib.parse.urlparse(uri or "").hostname or "").lower()
    except ValueError:
        return ""


def set_site_value(settings, host, key, value, sub=None):
    """Set (or, when value is None, clear) a per-site value; drops empty entries."""
    if not host:
        return
    entry = settings.setdefault(host, {})
    if sub is None:
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    else:
        group = entry.setdefault(key, {})
        if value is None:
            group.pop(sub, None)
        else:
            group[sub] = value
        if not group:
            entry.pop(key, None)
    if not entry:
        settings.pop(host, None)


def get_site_permission(settings, host, kind):
    value = (settings.get(host, {}).get("permissions") or {}).get(kind)
    return value if value in ("allow", "deny") else None


# ---------------------------------------------------------------------------
# Tracker list updates (EasyPrivacy domain rules -> WebKit content blocker)
# ---------------------------------------------------------------------------
TRACKER_LIST_URL = "https://easylist.to/easylist/easyprivacy.txt"
TRACKER_LIST_CACHE = os.path.join(CACHE_DIR, "tracker_list.json")
TRACKER_LIST_MAX_AGE = 7 * 24 * 3600
TRACKER_LIST_MAX_BYTES = 8 * 1024 * 1024
TRACKER_LIST_MAX_DOMAINS = 50000
# Never block these even if a list says so: blocking them breaks sign-in, captchas
# and a huge number of sites, which is a worse outcome than a missed tracker.
_NEVER_BLOCK_SUBTREES = {
    "google.com", "gstatic.com", "googleapis.com", "recaptcha.net", "youtube.com", "googlevideo.com",
    "cloudflare.com", "jsdelivr.net", "unpkg.com", "bootstrapcdn.com", "github.com", "githubusercontent.com",
    "wikipedia.org", "wikimedia.org", "microsoft.com", "live.com", "apple.com", "amazon.com", "paypal.com",
}
_ABP_DOMAIN_RE = re.compile(r"^\|\|([a-z0-9][a-z0-9.-]*\.[a-z]{2,})\^(?:\$([a-z0-9,~=_|.-]+))?$")
_ABP_SAFE_OPTIONS = {"third-party", "3p", "script", "image", "xmlhttprequest", "xhr", "ping", "subdocument",
                     "stylesheet", "font", "media", "other", "important"}


def parse_abp_domain_rules(text):
    """Extract plain `||domain^` blocking rules from an Adblock-Plus-syntax list.
    Anything fancier (paths, wildcards, exceptions, $domain=, cosmetic rules)
    is skipped on purpose: only whole-domain tracker rules are applied."""
    domains = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line[0] in "![#" or line.startswith("@@") or "##" in line or "#@#" in line:
            continue
        m = _ABP_DOMAIN_RE.match(line)
        if not m:
            continue
        options = m.group(2)
        if options:
            parts = set(options.split(","))
            if any(p.startswith(("domain=", "~")) for p in parts) or not parts <= _ABP_SAFE_OPTIONS:
                continue
        domain = m.group(1)
        if _host_matches_domain_set(domain, _NEVER_BLOCK_SUBTREES):
            continue
        domains.add(domain)
    return domains


def load_tracker_list_cache():
    """(domains, fetched_epoch) from the last successful download, or (set(), 0)."""
    try:
        with open(TRACKER_LIST_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        domains = {d for d in data.get("domains", []) if isinstance(d, str)}
        return domains, float(data.get("fetched", 0))
    except Exception:
        return set(), 0.0


def fetch_tracker_list(url=TRACKER_LIST_URL, timeout=20):
    """Download + parse the tracker list and cache the result. Raises on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "BharatBrowser-tracker-list"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(TRACKER_LIST_MAX_BYTES + 1)
    if len(raw) > TRACKER_LIST_MAX_BYTES:
        raise ValueError("tracker list too large")
    domains = parse_abp_domain_rules(raw.decode("utf-8", errors="replace"))
    if len(domains) < 100:
        raise ValueError(f"tracker list looks wrong ({len(domains)} domains)")
    domains = set(sorted(domains)[:TRACKER_LIST_MAX_DOMAINS])
    os.makedirs(CACHE_DIR, exist_ok=True)
    _write_json_private(TRACKER_LIST_CACHE, {"fetched": time.time(), "domains": sorted(domains)}, indent=None)
    return domains


# ---------------------------------------------------------------------------
# Usage statistics for the privacy report
# ---------------------------------------------------------------------------
STATS_FILE = os.path.join(CONFIG_DIR, "stats.json")


def load_privacy_stats():
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"since": float(data.get("since", time.time())), "blocked": int(data.get("blocked", 0)),
                "params": int(data.get("params", 0)), "https": int(data.get("https", 0))}
    except Exception:
        return {"since": time.time(), "blocked": 0, "params": 0, "https": 0}


def save_privacy_stats(stats):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        _write_json_private(STATS_FILE, stats, indent=None)
    except Exception as e:
        print("Stats save note:", e)


# ---------------------------------------------------------------------------
# Password storage: freedesktop Secret Service (GNOME Keyring, KWallet, KeePassXC)
# ---------------------------------------------------------------------------
class SecretServiceClient:
    """Minimal Secret Service client over Gio D-Bus. Passwords live only in the
    user's system keyring, never in Bharat Browser's own files. Every method
    returns a failure value (False/[]/None) instead of raising, and `.error`
    explains why when no keyring is available."""
    SERVICE = "org.freedesktop.secrets"
    SERVICE_PATH = "/org/freedesktop/secrets"

    def __init__(self, application="bharat-browser"):
        self.application = application
        self.error = ""
        self._bus = None
        self._session = None
        self._collection = None

    def _call(self, path, iface, method, params, reply_type):
        return self._bus.call_sync(self.SERVICE, path, iface, method, params,
                                   GLib.VariantType(reply_type) if reply_type else None,
                                   Gio.DBusCallFlags.NONE, 10000, None)

    def available(self):
        if self._session:
            return True
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            out = self._call(self.SERVICE_PATH, "org.freedesktop.Secret.Service", "OpenSession",
                             GLib.Variant("(sv)", ("plain", GLib.Variant("s", ""))), "(vo)").unpack()
            collection = self._call(self.SERVICE_PATH, "org.freedesktop.Secret.Service", "ReadAlias",
                                    GLib.Variant("(s)", ("default",)), "(o)").unpack()[0]
            if collection == "/":
                raise RuntimeError("no default keyring found")
            self._session, self._collection = out[1], collection
            return True
        except Exception as e:
            self.error = str(e)
            self._session = None
            return False

    def _locked(self, path, iface):
        value = self._call(path, "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", (f"org.freedesktop.Secret.{iface}", "Locked")), "(v)").unpack()[0]
        return bool(value)

    def _run_prompt(self, prompt_path):
        """Let the keyring show its own unlock dialog and wait for the outcome."""
        loop, result = GLib.MainLoop(), {}

        def on_completed(conn, sender, path, iface, name, params, *args):
            result["dismissed"] = params.unpack()[0]
            loop.quit()

        sub = self._bus.signal_subscribe(self.SERVICE, "org.freedesktop.Secret.Prompt", "Completed",
                                         prompt_path, None, Gio.DBusSignalFlags.NONE, on_completed)
        try:
            self._call(prompt_path, "org.freedesktop.Secret.Prompt", "Prompt", GLib.Variant("(s)", ("",)), None)
            GLib.timeout_add_seconds(120, lambda: (loop.quit(), False)[1])
            loop.run()
        finally:
            self._bus.signal_unsubscribe(sub)
        return result.get("dismissed", True) is False

    def _unlock(self, objects):
        _, prompt = self._call(self.SERVICE_PATH, "org.freedesktop.Secret.Service", "Unlock",
                               GLib.Variant("(ao)", (objects,)), "(aoo)").unpack()
        return prompt == "/" or self._run_prompt(prompt)

    def store(self, host, username, password):
        """Create or replace the login for (host, username)."""
        if not self.available():
            return False
        try:
            if self._locked(self._collection, "Collection") and not self._unlock([self._collection]):
                return False
            attrs = {"application": self.application, "host": host, "username": username}
            props = {"org.freedesktop.Secret.Item.Label": GLib.Variant("s", f"Bharat Browser: {username} @ {host}"),
                     "org.freedesktop.Secret.Item.Attributes": GLib.Variant("a{ss}", attrs)}
            secret = (self._session, b"", password.encode("utf-8"), "text/plain")
            _, prompt = self._call(self._collection, "org.freedesktop.Secret.Collection", "CreateItem",
                                   GLib.Variant("(a{sv}(oayays)b)", (props, secret, True)), "(oo)").unpack()
            return prompt == "/" or self._run_prompt(prompt)
        except Exception as e:
            self.error = str(e)
            return False

    def find(self, host=None):
        """Saved logins (optionally for one host) as [{"item", "host", "username"}], no passwords."""
        if not self.available():
            return []
        try:
            attrs = {"application": self.application}
            if host:
                attrs["host"] = host
            unlocked, locked = self._call(self.SERVICE_PATH, "org.freedesktop.Secret.Service", "SearchItems",
                                          GLib.Variant("(a{ss})", (attrs,)), "(aoao)").unpack()
            if locked and self._unlock(locked):
                unlocked = list(unlocked) + list(locked)
            results = []
            for item in unlocked:
                a = self._call(item, "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", ("org.freedesktop.Secret.Item", "Attributes")), "(v)").unpack()[0]
                results.append({"item": item, "host": a.get("host", ""), "username": a.get("username", "")})
            return sorted(results, key=lambda r: (r["host"], r["username"]))
        except Exception as e:
            self.error = str(e)
            return []

    def get_password(self, item):
        try:
            secret = self._call(item, "org.freedesktop.Secret.Item", "GetSecret",
                                GLib.Variant("(o)", (self._session,)), "((oayays))").unpack()[0]
            return bytes(secret[2]).decode("utf-8")
        except Exception as e:
            self.error = str(e)
            return None

    def delete(self, item):
        try:
            (prompt,) = self._call(item, "org.freedesktop.Secret.Item", "Delete", None, "(o)").unpack()
            return prompt == "/" or self._run_prompt(prompt)
        except Exception as e:
            self.error = str(e)
            return False


# Runs in an isolated JS world (see create_new_tab) so page scripts can't see or
# spoof it. It only *reports* what the user typed when they submit a login form;
# nothing is ever filled in without a click on the "Fill" bar.
PASSWORD_DETECT_JS = r"""
(function(){
 if (window.top !== window) return;
 function post(m){ try { window.webkit.messageHandlers.bharatPw.postMessage(m); } catch(e){} }
 var sent = {};
 function visible(el){ var r = el.getBoundingClientRect(), s = getComputedStyle(el);
   return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; }
 function pwFields(root){ return Array.prototype.filter.call(root.querySelectorAll('input[type=password]'), visible); }
 function userFor(pw){
   var scope = pw.form || document, best = null;
   scope.querySelectorAll('input:not([type]),input[type=text],input[type=email],input[type=tel]').forEach(function(el){
     if (visible(el) && (el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING)) best = el; });
   return best; }
 function capture(pw){
   var u = userFor(pw), user = u ? u.value : '', pass = pw.value;
   if (!pass) return;
   var key = user + '\u0000' + pass; if (sent[key]) return; sent[key] = 1;
   post({type: 'submit', user: user, pass: pass}); }
 document.addEventListener('submit', function(e){
   if (e.target && e.target.querySelectorAll) pwFields(e.target).forEach(capture); }, true);
 document.addEventListener('click', function(e){
   var b = e.target.closest && e.target.closest('button,input[type=submit],[role=button]'); if (!b) return;
   pwFields(b.form || b.closest('form') || document).forEach(capture); }, true);
 document.addEventListener('keydown', function(e){
   if (e.key === 'Enter' && e.target && e.target.type === 'password')
     pwFields(e.target.form || document).forEach(capture); }, true);
 window.addEventListener('load', function(){
   setTimeout(function(){ if (pwFields(document).length) post({type: 'form'}); }, 300); });
})();
"""

# Run in the page's own world, only after the user clicks "Fill" (a real gesture).
# Uses the native value setter + events so frameworks such as React notice the change.
PASSWORD_FILL_JS = r"""
(function(user, pass){
 function visible(el){ var r = el.getBoundingClientRect(), s = getComputedStyle(el);
   return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; }
 function setv(el, v){
   var d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
   d.set.call(el, v);
   el.dispatchEvent(new Event('input', {bubbles: true}));
   el.dispatchEvent(new Event('change', {bubbles: true})); }
 var pw = Array.prototype.filter.call(document.querySelectorAll('input[type=password]'), visible)[0];
 if (!pw) return false;
 var scope = pw.form || document, best = null;
 scope.querySelectorAll('input:not([type]),input[type=text],input[type=email],input[type=tel]').forEach(function(el){
   if (visible(el) && (el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING)) best = el; });
 if (best && user) setv(best, user);
 setv(pw, pass);
 return true;
})(%s, %s);
"""

# ---------------------------------------------------------------------------
# Reader mode: builds a clean, readable copy of the article in a closed shadow
# root and removes it again on the second call. Returns 'opened', 'closed' or
# 'no-article'. Only whitelisted tags/attributes are copied, so no page script,
# event handler or style from the original can run inside the reader.
# ---------------------------------------------------------------------------
READER_MODE_JS = r"""
(function(){
 var OID = '__bharat_reader', old = document.getElementById(OID);
 if (old) { old.remove(); document.documentElement.style.overflow = window.__bharatPrevOverflow || ''; return 'closed'; }
 var DROP = {SCRIPT:1,STYLE:1,NOSCRIPT:1,IFRAME:1,FORM:1,BUTTON:1,INPUT:1,SELECT:1,TEXTAREA:1,SVG:1,CANVAS:1,
   VIDEO:1,AUDIO:1,OBJECT:1,EMBED:1,NAV:1,ASIDE:1,FOOTER:1,TEMPLATE:1,DIALOG:1};
 var KEEP = {P:1,H1:1,H2:1,H3:1,H4:1,H5:1,H6:1,UL:1,OL:1,LI:1,BLOCKQUOTE:1,PRE:1,CODE:1,A:1,IMG:1,FIGURE:1,
   FIGCAPTION:1,STRONG:1,B:1,EM:1,I:1,BR:1,HR:1,TABLE:1,THEAD:1,TBODY:1,TR:1,TD:1,TH:1,SUB:1,SUP:1};
 var BAD = /(^|[\s_-])(ad|ads|advert|banner|comment|comments|cookie|footer|menu|modal|newsletter|popup|promo|related|share|sharing|sidebar|social|sponsor|subscribe|widget)([\s_-]|$)/i;
 function tl(el){ return (el.innerText || '').trim().length; }
 function ll(el){ var n = 0; el.querySelectorAll('a').forEach(function(a){ n += (a.innerText || '').length; }); return n; }
 var scores = new Map();
 document.querySelectorAll('p').forEach(function(p){
   var t = (p.innerText || '').trim().length;
   if (t < 40 || p.closest('nav,aside,footer,form')) return;
   var par = p.parentElement; if (!par) return;
   scores.set(par, (scores.get(par) || 0) + t);
   var gp = par.parentElement; if (gp) scores.set(gp, (scores.get(gp) || 0) + t / 2); });
 var best = null, bs = 0;
 scores.forEach(function(s, el){
   var len = tl(el) || 1, cls = (el.className && el.className.baseVal === undefined ? el.className : '') + ' ' + (el.id || '');
   s *= (1 - Math.min(1, ll(el) / len));
   if (/article|main|content|post|entry|story/i.test(cls)) s *= 1.2;
   if (BAD.test(cls)) s *= 0.3;
   if (s > bs) { bs = s; best = el; } });
 if (!best || bs < 250) return 'no-article';
 function clean(node, out){
   node.childNodes.forEach(function(c){
     if (c.nodeType === 3) { out.appendChild(document.createTextNode(c.nodeValue)); return; }
     if (c.nodeType !== 1) return;
     var tag = c.tagName.toUpperCase();
     if (DROP[tag] || c.hidden || c.getAttribute('aria-hidden') === 'true') return;
     var cls = (typeof c.className === 'string' ? c.className : '') + ' ' + (c.id || '');
     if (tag !== 'P' && BAD.test(cls) && tl(c) < 600) return;
     if (KEEP[tag]) {
       var e = document.createElement(tag);
       if (tag === 'A') { var h = c.href; if (/^https?:/i.test(h)) { e.href = h; e.rel = 'noopener noreferrer'; } }
       if (tag === 'IMG') { var src = c.currentSrc || c.src; if (!/^https?:|^data:image\//i.test(src || '')) return; e.src = src; e.alt = c.alt || ''; }
       clean(c, e); out.appendChild(e);
     } else clean(c, out); }); }
 var body = document.createElement('div'); clean(best, body);
 var h1 = best.querySelector('h1') || document.querySelector('h1');
 var title = (h1 && h1.innerText.trim()) || document.title || '';
 var dup = body.querySelector('h1');   // the page's own headline is shown once, as our title
 if (dup && dup.textContent.trim() === title) dup.remove();
 var by = document.querySelector('meta[name=author]');
 var host = document.createElement('div'); host.id = OID;
 host.style.cssText = 'position:fixed;inset:0;z-index:2147483647;';
 var root = host.attachShadow({mode: 'closed'});
 var themes = [['#fbfbf8', '#222', '#0b57d0'], ['#f4ecd8', '#3b3226', '#8a4b08'], ['#14171c', '#d8dde6', '#8ab4f8']];
 var ti = 0, fs = 19;
 var st = document.createElement('style');
 st.textContent = '.wrap{position:absolute;inset:0;overflow:auto;font-family:Georgia,"Noto Serif",serif;line-height:1.7}' +
  '.bar{position:sticky;top:0;display:flex;gap:8px;justify-content:flex-end;padding:10px 16px;font:14px system-ui,sans-serif}' +
  '.bar button{border:1px solid currentColor;background:transparent;color:inherit;border-radius:999px;padding:4px 12px;cursor:pointer;opacity:.75}' +
  '.bar button:hover{opacity:1}.col{max-width:680px;margin:0 auto;padding:10px 22px 80px}' +
  'h1.t{font-size:1.9em;line-height:1.25;margin:.4em 0 .2em}.by{font:14px system-ui,sans-serif;opacity:.7;margin-bottom:1.5em}' +
  'img{max-width:100%;height:auto}pre{overflow:auto;padding:12px;background:rgba(127,127,127,.15)}' +
  'blockquote{border-left:3px solid currentColor;margin-left:0;padding-left:16px;opacity:.85}table{border-collapse:collapse}td,th{border:1px solid rgba(127,127,127,.4);padding:4px 8px}';
 var wrap = document.createElement('div'); wrap.className = 'wrap';
 var bar = document.createElement('div'); bar.className = 'bar';
 function btn(label, fn){ var b = document.createElement('button'); b.textContent = label; b.onclick = fn; bar.appendChild(b); }
 function apply(){ var t = themes[ti]; wrap.style.background = t[0]; wrap.style.color = t[1]; wrap.style.fontSize = fs + 'px';
   col.querySelectorAll('a').forEach(function(a){ a.style.color = t[2]; }); }
 btn('A−', function(){ fs = Math.max(13, fs - 2); apply(); });
 btn('A+', function(){ fs = Math.min(32, fs + 2); apply(); });
 btn('Theme', function(){ ti = (ti + 1) % themes.length; apply(); });
 function close(){ host.remove(); document.documentElement.style.overflow = window.__bharatPrevOverflow || ''; }
 btn('✕ Close', close);
 var col = document.createElement('div'); col.className = 'col';
 var t = document.createElement('h1'); t.className = 't'; t.textContent = title; col.appendChild(t);
 if (by && by.content) { var b2 = document.createElement('div'); b2.className = 'by'; b2.textContent = by.content; col.appendChild(b2); }
 col.appendChild(body);
 wrap.appendChild(bar); wrap.appendChild(col); root.appendChild(st); root.appendChild(wrap);
 window.__bharatPrevOverflow = document.documentElement.style.overflow;
 document.documentElement.style.overflow = 'hidden';
 document.addEventListener('keydown', function esc(e){ if (e.key === 'Escape' && document.getElementById(OID)) { close(); document.removeEventListener('keydown', esc, true); } }, true);
 document.documentElement.appendChild(host); apply();
 return 'opened';
})();
"""

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
        self.current_version = APP_VERSION
        self.is_private = private
        title_suffix = " (Private)" if private else ""
        super().__init__(title=f"Bharat Browser v{self.current_version}{title_suffix}")
        self._private_windows = []
        global _LIVE_WINDOW_COUNT
        _LIVE_WINDOW_COUNT += 1
        self.set_default_size(1280, 850)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Prune stale Chromium-engine cache remnants
        if not self.is_private:
            self._cleanup_stale_chromium_artifacts()

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
        self._shield_badge_update_scheduled = False
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
        self.spellcheck_enabled = saved_settings.get("spellcheck_enabled", True)
        self.tracker_lists_enabled = saved_settings.get("tracker_lists_enabled", True)
        self.passwords_enabled = saved_settings.get("passwords_enabled", True)
        # Per-site choices (permissions, zoom, ad blocking, JS) and privacy stats are
        # only persisted for normal windows; private windows keep them in memory.
        self.site_settings = {} if private else load_site_settings()
        self.stats = {"since": time.time(), "blocked": 0, "params": 0, "https": 0} if private else load_privacy_stats()
        self.session_stats = {"blocked": 0, "params": 0, "https": 0}
        self.blocked_domains = Counter()
        self.blocked_sites = Counter()
        self._closed_tabs = []
        self._http_allowed_hosts = set()
        self._http_tokens = {}
        self._recent_https_upgrades = {}
        self._fill_offered = set()
        self._infobar = None
        self.secrets = SecretServiceClient()
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

        # Cookies are memory-only by default, so logins would be lost on every
        # restart. Persist them for normal windows (private windows use the
        # ephemeral manager above and must stay in memory), and block
        # third-party cookies plus enable Intelligent Tracking Prevention.
        try:
            cookie_mgr = self.context.get_cookie_manager()
            if not self.is_private:
                cookie_mgr.set_persistent_storage(
                    os.path.join(CONFIG_DIR, "cookies.sqlite"),
                    WebKit2.CookiePersistentStorage.SQLITE
                )
            cookie_mgr.set_accept_policy(WebKit2.CookieAcceptPolicy.NO_THIRD_PARTY)
            if hasattr(self.data_mgr, 'set_itp_enabled'):
                self.data_mgr.set_itp_enabled(True)
        except Exception as e:
            print("Cookie policy setup note:", e)

        try:
            self.context.set_spell_checking_enabled(self.spellcheck_enabled)
            languages = [lang for lang in GLib.get_language_names() if "." not in lang and lang != "C"][:2]
            if languages:
                self.context.set_spell_checking_languages(languages)
            self.context.register_uri_scheme("bharat", self._on_bharat_scheme, None)
        except Exception as e:
            print("Spell-check / bharat:// setup note:", e)

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
        if hasattr(self.web_settings, 'set_enable_back_forward_navigation_gestures'):
            self.web_settings.set_enable_back_forward_navigation_gestures(True)
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
        self.web_settings.set_user_agent(USER_AGENT)

        # Main Overlay
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.overlay.add(main_vbox)

        # Top Navigation Bar
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.top_bar = top_bar
        top_bar.get_style_context().add_class("top-bar")
        top_bar.set_margin_start(10)
        top_bar.set_margin_end(10)
        top_bar.set_margin_top(3)
        top_bar.set_margin_bottom(3)
        main_vbox.pack_start(top_bar, False, False, 0)
        self.infobar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_vbox.pack_start(self.infobar_box, False, False, 0)

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
        self.btn_shield.set_tooltip_text("Shield active — click for your Privacy Report")
        self.btn_shield.connect("clicked", lambda b: self.open_privacy_report())
        top_bar.pack_start(self.btn_shield, False, False, 0)

        self.btn_settings = Gtk.Button.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        self.btn_settings.get_style_context().add_class("flat-icon-btn")
        self.btn_settings.set_tooltip_text("Menu")
        self.btn_settings.connect("clicked", self.show_main_menu)
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

        # Login-form detection runs in an isolated JS world so pages can't see or spoof it.
        self.password_script = None
        if not self.is_private and hasattr(WebKit2.UserScript, "new_for_world"):
            self.password_script = WebKit2.UserScript.new_for_world(
                PASSWORD_DETECT_JS, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.END, "bharat-pw", None, None)

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
        self.push_notification_status(f"Bharat Browser v{self.current_version} Ready | Made in INDIA")
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
        restored_pins = []
        if self.is_private:
            initial_urls = [self.homepage]
        elif self.open_homepage_on_startup:
            initial_urls = [self.homepage]
        else:
            saved_session = load_session_state()
            restored_pins = [i for i in saved_session.get("pinned", []) if isinstance(i, int)]
            initial_urls = saved_session.get("urls", [])
            if isinstance(initial_urls, str):
                initial_urls = [initial_urls]
            if not initial_urls:
                initial_urls = [self.homepage]

        for url in initial_urls:
            self.create_new_tab(url)
        for index in restored_pins:
            if 0 <= index < self.notebook.get_n_pages():
                self.set_tab_pinned(self.notebook.get_nth_page(index), True)

        # Refresh the downloaded tracker list in the background if it's stale.
        if not self.is_private:
            GLib.timeout_add_seconds(20, self._maybe_refresh_tracker_list)

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

    def _cleanup_stale_chromium_artifacts(self):
        stale_dirs = [
            "GPUCache", "DawnWebGPUCache", "DawnGraphiteCache",
            "Shared Dictionary", "Code Cache", "Crashpad",
            "Trust Tokens", "WebStorage", "blob_storage", "DIPS"
        ]
        for d in stale_dirs:
            target = os.path.join(CONFIG_DIR, d)
            if os.path.isdir(target):
                try:
                    shutil.rmtree(target, ignore_errors=True)
                except Exception:
                    pass
        try:
            for item in os.listdir(CONFIG_DIR):
                if item.startswith(".org.chromium"):
                    target = os.path.join(CONFIG_DIR, item)
                    try:
                        os.remove(target)
                    except Exception:
                        pass
        except Exception:
            pass

    def _detect_gpu_info_async(self):
        self.gpu_info_label = detect_gpu_info()

    def on_low_memory_warning(self, monitor, level):
        print(f"Low-memory warning (level={level}), trimming caches.")
        try:
            self.context.clear_cache()
        except Exception as e:
            print("Cache trim note:", e)

    BASE_FILTER_ID = "bharat-adblock-v2"

    def _compile_content_blocker_filter(self):
        """Install the native content blocker. Compiled filters persist in WebKit's
        store, so a launch normally just loads one (instant); compiling the large
        downloaded tracker list takes ~10s and only happens when the list changes.
        The small built-in rules go in first so the browser is never unprotected
        while the big list compiles."""
        try:
            store_dir = os.path.join(CACHE_DIR, "content-filters")
            os.makedirs(store_dir, exist_ok=True)
            store = WebKit2.UserContentFilterStore.new(store_dir)
            extra, fetched = load_tracker_list_cache() if self.tracker_lists_enabled else (set(), 0.0)
            full_id = self.BASE_FILTER_ID + (f"-{int(fetched)}-{len(extra)}" if extra else "")
            keep = {self.BASE_FILTER_ID, full_id}
            if self.content_filter is None:
                self._load_or_compile_filter(store, self.BASE_FILTER_ID, set(), keep)
            if extra:
                self._load_or_compile_filter(store, full_id, extra, keep)
        except Exception as e:
            print("Content filter compile note:", e)

    def _load_or_compile_filter(self, store, identifier, extra, keep):
        store.load(identifier, None, self._on_content_filter_loaded, (store, identifier, extra, keep))

    def _on_content_filter_loaded(self, store, result, data):
        _, identifier, extra, keep = data
        try:
            self._install_content_filter(store, store.load_finish(result), len(extra), keep)
        except Exception:
            # Not compiled yet (first run, or the tracker list changed): compile in the background.
            try:
                rules = build_content_blocker_rules_json(extra)
                store.save(identifier, GLib.Bytes.new(rules), None, self._on_content_filter_saved,
                           (len(extra), keep))
            except Exception as e:
                print("Content filter compile note:", e)

    def _on_content_filter_saved(self, store, result, data):
        rank, keep = data
        try:
            self._install_content_filter(store, store.save_finish(result), rank, keep)
        except Exception as e:
            print("Content filter save note:", e)

    def _install_content_filter(self, store, content_filter, rank, keep):
        if rank < getattr(self, "_content_filter_rank", -1):
            return  # a fuller filter is already active; don't replace it with a smaller one
        self._content_filter_rank = rank
        previous = self.content_filter
        self.content_filter = content_filter
        # Swap the new filter into every open tab (and cover tabs opened before
        # compilation finished), honouring each tab's per-site ad-block choice.
        for i in range(self.notebook.get_n_pages()):
            tb = self.notebook.get_nth_page(i)
            if hasattr(tb, '_bharat_webview'):
                wv = tb._bharat_webview
                ucm = wv.get_user_content_manager()
                if previous is not None and getattr(wv, "_bharat_filter_on", False):
                    ucm.remove_filter(previous)
                wants = self._site_wants_filter(site_host_of(wv.get_uri()))
                if wants:
                    ucm.add_filter(content_filter)
                wv._bharat_filter_on = wants
        print(f"Native ad/tracker content-blocker active ({rank:,} downloaded tracker domains).")
        # Drop compiled filters left behind by earlier tracker-list versions.
        store.fetch_identifiers(None, self._prune_content_filters, keep)

    def _prune_content_filters(self, store, result, keep):
        try:
            for ident in store.fetch_identifiers_finish(result) or []:
                if ident not in keep and ident.startswith("bharat-adblock-"):
                    store.remove(ident, None, lambda s, r, d: None, None)
        except Exception as e:
            print("Content filter prune note:", e)

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
        /* --- Settings polish: header banner, icon bubbles, switches, chips --- */
        .bharat-dialog .settings-title { color: #ffffff; font-size: 22px; font-weight: 800; }
        .bharat-dialog .settings-subtitle { color: #94a3b8; font-size: 12px; }
        .tricolor-strip {
            min-height: 4px;
            border-radius: 999px;
            background-image: linear-gradient(to right, #ff9933 0%, #ff9933 33%, #f8fafc 33%, #f8fafc 66%, #138808 66%, #138808 100%);
        }
        .bharat-dialog .settings-card {
            border-left: 3px solid #6366f1;
            background-image: linear-gradient(135deg, rgba(99, 102, 241, 0.07), rgba(255, 255, 255, 0.0) 55%);
        }
        .bharat-dialog .settings-section-title {
            color: #a5b4fc;
            letter-spacing: 1.5px;
            font-size: 11px;
        }
        .bharat-dialog .settings-icon-bubble {
            background-image: linear-gradient(135deg, rgba(99, 102, 241, 0.35), rgba(139, 92, 246, 0.25));
            border: 1px solid rgba(165, 180, 252, 0.35);
            border-radius: 12px;
            min-width: 36px;
            min-height: 36px;
            font-size: 17px;
        }
        .bharat-dialog switch, popover.bharat-menu switch {
            background-color: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 999px;
            min-width: 46px;
            min-height: 24px;
            color: transparent;
        }
        .bharat-dialog switch:checked, popover.bharat-menu switch:checked {
            background-image: linear-gradient(135deg, #6366f1, #8b5cf6);
            border-color: #818cf8;
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.45);
        }
        .bharat-dialog switch slider, popover.bharat-menu switch slider {
            background-color: #f8fafc;
            background-image: none;
            border: none;
            border-radius: 999px;
            min-width: 20px;
            min-height: 20px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.5);
        }
        .bharat-dialog .settings-chip {
            background-color: rgba(255, 255, 255, 0.07);
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 999px;
            color: #e2e8f0;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 10px;
        }
        .bharat-dialog .settings-chip-green {
            background-color: rgba(34, 197, 94, 0.14);
            border-color: rgba(34, 197, 94, 0.4);
            color: #86efac;
        }
        .bharat-dialog .settings-chip-saffron {
            background-color: rgba(255, 153, 51, 0.14);
            border-color: rgba(255, 153, 51, 0.4);
            color: #fdba74;
        }
        .bharat-dialog .settings-primary-btn, .settings-primary-btn {
            background-image: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: #ffffff;
            border: none;
            border-radius: 999px;
            padding: 8px 22px;
            font-size: 12px;
            font-weight: 700;
            box-shadow: 0 3px 12px rgba(99, 102, 241, 0.4);
        }
        .bharat-dialog .settings-primary-btn:hover, .settings-primary-btn:hover {
            background-image: linear-gradient(135deg, #7c7ff5, #a78bfa);
            box-shadow: 0 5px 16px rgba(99, 102, 241, 0.55);
        }
        .bharat-dialog .settings-action-btn { border-radius: 10px; }
        .bharat-dialog separator { background-color: rgba(255, 255, 255, 0.06); min-height: 1px; }
        popover.bharat-menu, popover.bharat-menu > * {
            background-color: #151a24;
            color: #f1f5f9;
            border-radius: 12px;
        }
        popover.bharat-menu label { color: #f1f5f9; }
        popover.bharat-menu modelbutton { color: #f1f5f9; padding: 6px 12px; border-radius: 8px; }
        popover.bharat-menu modelbutton:hover { background-color: rgba(99, 102, 241, 0.25); }
        .bharat-infobar {
            background-image: linear-gradient(135deg, #1e1b4b, #172554);
            border-bottom: 1px solid rgba(165, 180, 252, 0.35);
        }
        .bharat-infobar label { color: #e2e8f0; }
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
        webview._bharat_filter_on = self.content_filter is not None

        # 1. Media Codec Polyfill, 1b. Anti-Fingerprinting Farbling Engine,
        # 2. Smart Link Prefetching — shared, pre-built instances (see __init__)
        ucm.add_script(self.media_script)
        ucm.add_script(self.farbling_script)
        ucm.add_script(self.prefetch_script)
        if self.dark_mode_active:
            self._set_dark_stylesheet(ucm, True)

        # Signals
        sig_ids = []
        sig_ids.append((webview, webview.connect("load-changed", self.on_load_changed)))
        sig_ids.append((webview, webview.connect("mouse-target-changed", self.on_mouse_target_changed)))
        sig_ids.append((webview, webview.connect("notify::title", self.on_webview_title_notify)))
        sig_ids.append((webview, webview.connect("resource-load-started", self.on_resource_load_started)))
        sig_ids.append((webview, webview.connect("web-process-terminated", self.on_web_process_terminated)))
        sig_ids.append((webview, webview.connect("permission-request", self.on_permission_request)))
        sig_ids.append((webview, webview.connect("load-failed-with-tls-errors", self.on_load_failed_with_tls_errors)))
        sig_ids.append((webview, webview.connect("load-failed", self.on_load_failed)))
        # Files WebKit can't render inline (Office docs, zip archives, etc.)
        # would otherwise just interrupt the frame load and surface as a
        # confusing "page didn't load" error; convert them into a normal
        # download instead, same as clicking a download link would do.
        sig_ids.append((webview, webview.connect("decide-policy", self.on_decide_policy)))
        # Without this, WebKit silently drops any navigation that wants a new
        # window/tab (target="_blank", window.open(), middle-click, OAuth
        # popups, etc.) instead of doing anything visible.
        sig_ids.append((webview, webview.connect("create", self.on_create_webview)))
        sig_ids.append((webview, webview.connect("enter-fullscreen", self.on_webview_enter_fullscreen)))
        sig_ids.append((webview, webview.connect("leave-fullscreen", self.on_webview_leave_fullscreen)))

        # Ctrl+scroll to zoom. A Gtk.EventControllerScroll attached directly
        # to the webview (tried in a prior version, both CAPTURE and BUBBLE
        # phase) fully claims scroll input at the GTK controller-framework
        # level, which turned out to be mutually exclusive with WebKit's own
        # native page-scroll handling — it broke normal mouse-wheel/touchpad
        # scrolling entirely. The plain "scroll-event" signal doesn't have
        # that problem: returning False lets the event continue on to
        # WebKit's normal handling exactly like any unhandled GTK signal.
        webview.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        sig_ids.append((webview, webview.connect("scroll-event", self.on_webview_scroll)))

        find_controller = webview.get_find_controller()
        sig_ids.append((find_controller, find_controller.connect("found-text", self.on_find_found_text, webview)))
        sig_ids.append((find_controller, find_controller.connect("failed-to-find-text", self.on_find_failed_text, webview)))
        webview._bharat_sig_ids = sig_ids
        self._setup_password_detection(webview)

        tab_box.pack_start(webview, True, True, 0)
        tab_box.show_all()

        # Custom Tab Header Widget (Label + Close Button)
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab_label = Gtk.Label(label="New Tab")
        # width_chars gives each tab a real minimum width; without it GTK squeezes
        # an ellipsized label down to "…" in a scrollable notebook.
        tab_label.set_width_chars(12)
        tab_label.set_max_width_chars(18)
        tab_label.set_ellipsize(3) # PANGO_ELLIPSIZE_END
        
        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_tooltip_text("Close Tab")
        close_btn.connect("clicked", lambda b: self.close_tab(tab_box))

        header_box.pack_start(tab_label, True, True, 0)
        header_box.pack_start(close_btn, False, False, 0)
        header_box.show_all()

        # EventBox so right-click (menu) and middle-click (close) work on the tab header.
        header_event = Gtk.EventBox()
        header_event.set_visible_window(False)
        header_event.add(header_box)
        header_event.connect("button-press-event", lambda w, e: self._on_tab_header_click(tab_box, e))
        header_event.show()

        tab_box._bharat_webview = webview
        tab_box._bharat_label = tab_label
        tab_box._bharat_close_btn = close_btn
        tab_box._bharat_pinned = False

        page_num = self.notebook.append_page(tab_box, header_event)
        self.notebook.set_tab_reorderable(tab_box, True)
        self.notebook.set_current_page(page_num)

        if load_initial_uri:
            webview.load_uri(url or self.homepage)
        return webview

    def on_webview_enter_fullscreen(self, webview):
        # Returning False lets WebKit's default handler fullscreen the toplevel
        # window; we only need to hide our own chrome so the video fills it.
        self.top_bar.hide()
        self.notebook.set_show_tabs(False)
        self.statusbar.hide()
        return False

    def on_webview_leave_fullscreen(self, webview):
        self.top_bar.show()
        self._update_tabs_visibility(self.notebook)
        self.statusbar.show()
        return False

    def print_active_page(self):
        webview = self.get_active_webview()
        if webview:
            WebKit2.PrintOperation.new(webview).run_dialog(self)

    def toggle_inspector(self):
        webview = self.get_active_webview()
        if not webview or not self.dev_tools_enabled:
            return
        inspector = webview.get_inspector()
        if getattr(webview, '_bharat_inspector_open', False):
            inspector.close()
            return
        webview._bharat_inspector_open = True
        if not getattr(webview, '_bharat_inspector_hooked', False):
            webview._bharat_inspector_hooked = True
            inspector.connect("closed", lambda i: setattr(webview, '_bharat_inspector_open', False))
        inspector.show()

    def switch_tab(self, step):
        n = self.notebook.get_n_pages()
        if n > 1:
            self.notebook.set_current_page((self.notebook.get_current_page() + step) % n)

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

    def _collect_session(self):
        """(urls, indexes of pinned tabs within urls)."""
        urls, pinned = [], []
        for i in range(self.notebook.get_n_pages()):
            tb = self.notebook.get_nth_page(i)
            if hasattr(tb, '_bharat_webview'):
                # A suspended tab's live webview URI is "about:blank"; use the
                # URI it was suspended at so its session-restore entry survives.
                u = self._suspended_tab_uris.get(id(tb)) or tb._bharat_webview.get_uri()
                if u and not u.startswith("about:"):
                    if getattr(tb, "_bharat_pinned", False):
                        pinned.append(len(urls))
                    urls.append(u)
        return urls, pinned

    def _collect_session_urls(self):
        return self._collect_session()[0]

    def _run_session_save(self):
        self._session_save_source = None
        if not self.is_private:
            urls, pinned = self._collect_session()
            if urls:
                save_session_state(urls, pinned)
            save_privacy_stats(self.stats)
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
        if not self.is_private:
            save_privacy_stats(self.stats)

        global _LIVE_WINDOW_COUNT
        _LIVE_WINDOW_COUNT -= 1
        if _LIVE_WINDOW_COUNT <= 0:
            Gtk.main_quit()

    def close_tab(self, tab_box):
        webview = getattr(tab_box, '_bharat_webview', None)
        if webview is not None and not self.is_private:
            closed_uri = self._suspended_tab_uris.get(id(tab_box)) or webview.get_uri() or ""
            if closed_uri and not closed_uri.startswith("about:"):
                self._closed_tabs.append({"url": closed_uri})
                del self._closed_tabs[:-20]
        if webview is not None:
            self._crash_counts.pop(id(webview), None)
            self._load_failure_counts.pop(id(webview), None)
            self._flush_page_view(webview)
            if hasattr(self, '_page_view_start'):
                self._page_view_start.pop(id(webview), None)

            # Disconnect all attached signal handlers to avoid retaining references
            sig_ids = getattr(webview, '_bharat_sig_ids', [])
            for obj, sig_id in sig_ids:
                try:
                    if hasattr(obj, 'is_connected') and obj.is_connected(sig_id):
                        obj.disconnect(sig_id)
                except Exception:
                    pass
            webview._bharat_sig_ids = []

            # Stop loading and destroy webview
            try:
                webview.stop_loading()
            except Exception:
                pass

            try:
                webview.destroy()
            except Exception:
                pass

        if getattr(self, '_current_active_tab_box', None) == tab_box:
            self._current_active_tab_box = None

        self._tab_last_active.pop(id(tab_box), None)
        self._suspended_session_states.pop(id(tab_box), None)
        self._suspended_tab_titles.pop(id(tab_box), None)
        self._suspended_tab_uris.pop(id(tab_box), None)

        page_num = self.notebook.page_num(tab_box)
        if page_num != -1:
            self.notebook.remove_page(page_num)

        try:
            tab_box.destroy()
        except Exception:
            pass

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
        elif icon_pos == Gtk.EntryIconPosition.PRIMARY:
            self.show_site_popover()

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
        if reason == WebKit2.WebProcessTerminationReason.TERMINATED_BY_API:
            return  # we asked for it (tab closed / suspended): not a crash
        key = id(webview)
        count = self._crash_counts.get(key, 0) + 1
        self._crash_counts[key] = count
        print(f"Web process terminated (reason={reason}), crash #{count} for this tab")
        uri = webview.get_uri() or self.homepage
        safe_uri = html_module.escape(uri, quote=True)
        reload_btn = f'<a class="btn" href="{safe_uri}">Reload this page</a>'

        if reason == WebKit2.WebProcessTerminationReason.EXCEEDED_MEMORY:
            # Reloading straight away would just run out of memory again.
            self.statusbar.push(self.context_id, "⚠️ This tab ran out of memory")
            page = build_notice_page(
                "🧠", "This tab ran out of memory",
                "<p>The page used more memory than Bharat Browser allows, so it was stopped to keep the rest of your "
                "browser (and computer) running.</p><ul><li>Close tabs you aren't using</li>"
                "<li>Turn on <b>Low Memory Mode</b> in Settings → Performance</li></ul>",
                reload_btn, f"web process terminated: {reason.value_nick}")
            GLib.idle_add(lambda: webview.load_html(page, None))
            return

        if count > self.MAX_AUTO_RELOAD_CRASHES:
            self.statusbar.push(self.context_id, "⚠️ This tab crashed repeatedly and was not reloaded automatically.")
            page = build_notice_page(
                "💥", "This page keeps crashing",
                "<p>Bharat Browser stopped reloading it automatically after repeated crashes.</p>"
                "<p>Try again later, or open the site in a private window to rule out stored site data.</p>",
                reload_btn, f"web process terminated: {reason.value_nick}")
            GLib.idle_add(lambda: webview.load_html(page, None))
            return

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

        # We upgraded this http:// address to https:// and the site didn't answer:
        # say so and let the user choose, instead of a generic error.
        failing_host = site_host_of(failing_uri)
        if (self.https_enabled and failing_uri.startswith("https://") and failing_host
                and self._recently_upgraded_to_https(failing_host) and not looks_offline(error.message)):
            self._load_failure_counts.pop(key, None)
            self._show_https_warning(webview, failing_uri, error.message)
            return True

        if count <= self.MAX_AUTO_RETRY_LOAD_FAILURES:
            # Many of these (e.g. "Connection reset by peer" mid-TLS-handshake)
            # are transient and succeed on a plain retry, so retry once
            # silently before showing the user an error page.
            self.statusbar.push(self.context_id, f"⚠️ Load failed, retrying... ({error.message})")
            GLib.timeout_add(self.LOAD_RETRY_DELAY_MS, lambda: (webview.load_uri(failing_uri), False)[1])
            return True

        self._load_failure_counts.pop(key, None)
        host = urllib.parse.urlparse(failing_uri).hostname or failing_uri
        offline = looks_offline(error.message)
        error_html = build_error_page(host, failing_uri, error.message, offline)
        GLib.idle_add(lambda: webview.load_html(error_html, failing_uri))
        self.statusbar.push(self.context_id, "📡 No internet connection" if offline else f"⚠️ Failed to load {host}")
        return True

    def on_key_press(self, widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        shift = event.state & Gdk.ModifierType.SHIFT_MASK
        alt = event.state & Gdk.ModifierType.MOD1_MASK
        if ctrl and shift and event.keyval in (Gdk.KEY_t, Gdk.KEY_T):
            self.reopen_closed_tab()
            return True
        if ctrl and alt and event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.toggle_reader_mode()
            return True
        if event.keyval == Gdk.KEY_F12 or (ctrl and shift and event.keyval in (Gdk.KEY_i, Gdk.KEY_I)):
            self.toggle_inspector()
            return True
        if event.keyval == Gdk.KEY_F5:
            webview = self.get_active_webview()
            if webview:
                webview.reload()
            return True
        if alt and event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            webview = self.get_active_webview()
            if webview:
                if event.keyval == Gdk.KEY_Left:
                    webview.go_back()
                else:
                    webview.go_forward()
            return True
        if ctrl and event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.switch_tab(-1 if (shift or event.keyval == Gdk.KEY_ISO_Left_Tab) else 1)
            return True
        if ctrl:
            keyval = event.keyval
            if keyval in (Gdk.KEY_p, Gdk.KEY_P) and not shift:
                self.print_active_page()
                return True
            elif keyval in (Gdk.KEY_l, Gdk.KEY_L):
                self.url_entry.grab_focus()
                self.url_entry.select_region(0, -1)
                return True
            elif shift and keyval in (Gdk.KEY_n, Gdk.KEY_N):
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
        self._remember_zoom(webview)

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
                    GLib.idle_add(self.push_notification_status, "⚠️ Couldn't check for updates right now")
                    return
                data = json.loads(response.read(1 << 20).decode('utf-8'))
                remote_version = data.get("version", "").strip()
                remote_sha256 = data.get("sha256", "").strip().lower()
                remote_signature = data.get("signature", "").strip().lower()

            if not remote_version or self.compare_versions(remote_version, self.current_version) <= 0:
                print(f"Bharat Browser is up to date (v{self.current_version}).")
                GLib.idle_add(self.show_latest_version_notification)
                return

            print(f"Update available: v{self.current_version} -> v{remote_version}. Downloading...")
            installed = self.download_and_install_update(remote_version, remote_sha256, remote_signature)
            if installed:
                print(f"Update v{remote_version} downloaded and installed; restart to apply.")
            else:
                print(f"Update v{remote_version} available but not auto-installed.")
            GLib.idle_add(self.show_update_notification_dialog, remote_version, installed)
        except Exception as e:
            print("Git update check note:", e)
            # Never claim "latest version" when the check itself failed.
            msg = ("📡 No internet connection — couldn't check for updates"
                   if looks_offline(str(e)) else "⚠️ Couldn't check for updates right now")
            GLib.idle_add(self.push_notification_status, msg)

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
                    remote_signature = data.get("signature", "").strip().lower()

                if not remote_version or self.compare_versions(remote_version, self.current_version) <= 0:
                    GLib.idle_add(_done, f"✅ Bharat Browser is up to date (v{self.current_version}).", False, True)
                    return

                GLib.idle_add(lambda: status_lbl.set_markup(f"<i>⬇️ New version v{remote_version} found! Downloading update...</i>"))
                installed = self.download_and_install_update(remote_version, remote_sha256, remote_signature)
                if installed:
                    GLib.idle_add(_done, f"🎉 Version v{remote_version} installed successfully! Click 'Restart Now' to apply.", True, True)
                    GLib.idle_add(self.show_update_notification_dialog, remote_version, True)
                else:
                    GLib.idle_add(_done, f"⬆️ Version v{remote_version} is available on GitHub (Install via package manager or git pull).", False, True)
                    GLib.idle_add(self.show_update_notification_dialog, remote_version, False)
            except Exception as e:
                if looks_offline(str(e)):
                    GLib.idle_add(_done, "📡 No internet connection. Connect to the internet and try again.", False, True)
                else:
                    GLib.idle_add(_done, f"❌ Update check failed: {str(e)}", False, True)

        def _done(msg, show_restart, enable_btn):
            status_lbl.set_markup(f"<b>{GLib.markup_escape_text(msg)}</b>" if "✅" in msg or "🎉" in msg else GLib.markup_escape_text(msg))
            btn_check.set_sensitive(enable_btn)
            if show_restart:
                btn_restart.show()

        threading.Thread(target=_worker, daemon=True).start()

    MAX_UPDATE_SOURCE_BYTES = 5 * 1024 * 1024  # sanity cap; the script is ~60KB today

    def download_and_install_update(self, remote_version, remote_sha256="", remote_signature=""):
        """Download bharat_browser.py for the announced release tag and replace the
        running script in place. Only runs if the target file is writable by this
        user; otherwise the update is left for the system package manager / manual
        copy. Fetches from an immutable tag (not the mutable 'master' branch) and,
        when package.json publishes a "sha256" field for the release, verifies the
        downloaded bytes against it, and always requires a valid Ed25519
        "signature" (see verify_update_signature) before installing anything."""
        target_path = os.path.abspath(__file__)
        if not os.access(target_path, os.W_OK):
            # System-wide package install (root-owned): update a per-user copy instead, which the launcher prefers.
            try:
                os.makedirs(USER_INSTALL_DIR, exist_ok=True)
            except OSError as e:
                print(f"Update available but {target_path} is not writable and {USER_INSTALL_DIR} can't be created: {e}")
                return False
            target_path = os.path.join(USER_INSTALL_DIR, "bharat_browser.py")
            print(f"{os.path.abspath(__file__)} is not writable; installing the update to {target_path} instead.")
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
                print("Auto-update note: release did not publish a sha256 checksum; relying on the signature check.")

            # The checksum only proves the download matches package.json, which
            # comes from the same place; the signature proves the release was
            # made by whoever holds the private key. No valid signature, no install.
            if not verify_update_signature(remote_version, new_source, remote_signature):
                print(f"Auto-update install failed: v{remote_version} has a missing or invalid signature; refusing to install.")
                return False

            # Reject anything that isn't at least syntactically valid Python
            ast.parse(new_source.decode('utf-8'))

            tmp_path = target_path + ".update-tmp"
            with open(tmp_path, "wb") as f:
                f.write(new_source)
            os.chmod(tmp_path, 0o755)
            os.replace(tmp_path, target_path)
            self._installed_script_path = target_path
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
            script = getattr(self, '_installed_script_path', None) or os.path.abspath(__file__)
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
            if not is_local_network_host(host) and host not in self._http_allowed_hosts:
                new_uri = uri.replace("http://", "https://", 1)
                request.set_uri(new_uri)
                uri = new_uri
                if len(self._recent_https_upgrades) > 200:
                    self._recent_https_upgrades.clear()
                self._recent_https_upgrades[host] = time.monotonic()
                self._count_event("https")

        if self.clearurls_enabled:
            sanitized = sanitize_url(uri)
            if sanitized != uri:
                request.set_uri(sanitized)
                uri = sanitized
                self._count_event("params")

        if self._site_wants_filter(site_host_of(webview.get_uri())) and is_ad_or_tracker(uri):
            self.blocked_count += 1
            self._count_blocked(webview, uri)
            if not getattr(self, '_shield_badge_update_scheduled', False):
                self._shield_badge_update_scheduled = True
                GLib.timeout_add(250, self._flush_shield_badge_update)
            if ".js" in uri or "script" in uri:
                request.set_uri("data:application/javascript,")
            elif ".json" in uri:
                request.set_uri("data:application/json,{}")
            else:
                request.set_uri("data:text/plain,")

    def _flush_shield_badge_update(self):
        self._shield_badge_update_scheduled = False
        self.update_shield_badge()
        return False

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
                        pin = "📌 " if getattr(tab_box, "_bharat_pinned", False) else ""
                        tab_box._bharat_label.set_text(pin + title)
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
        if load_event in (WebKit2.LoadEvent.STARTED, WebKit2.LoadEvent.REDIRECTED):
            # Per-site ad blocking / JavaScript must match the page about to load.
            host = site_host_of(webview.get_uri())
            if host:
                self._apply_site_policy(webview, host)
        if load_event == WebKit2.LoadEvent.STARTED:
            self._fill_offered = {k for k in self._fill_offered if k[0] != id(webview)}
        elif load_event == WebKit2.LoadEvent.COMMITTED:
            self._apply_saved_zoom(webview)
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
            "download_dir": self.download_dir,
            "spellcheck_enabled": self.spellcheck_enabled,
            "tracker_lists_enabled": self.tracker_lists_enabled,
            "passwords_enabled": self.passwords_enabled
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

        # "🛡️ Ad Blocking" -> round icon bubble on the left + "Ad Blocking" title.
        icon_part, _, rest = title_text.partition(" ")
        if rest and not icon_part.isascii():
            bubble = Gtk.Label(label=icon_part)
            bubble.get_style_context().add_class("settings-icon-bubble")
            bubble.set_valign(Gtk.Align.CENTER)
            row.pack_start(bubble, False, False, 0)
            title_text = rest

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

        chk = Gtk.Switch()
        chk.set_active(bool(is_active))
        chk.set_valign(Gtk.Align.CENTER)
        if on_toggled_cb:
            chk.connect("notify::active", lambda sw, _pspec: on_toggled_cb(sw.get_active()))
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
        close_btn = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        close_btn.get_style_context().add_class("settings-primary-btn")
        dialog.set_default_response(Gtk.ResponseType.CLOSE)
        dialog.set_default_size(620, 640)

        content_area = dialog.get_content_area()
        content_area.set_margin_start(16)
        content_area.set_margin_end(16)
        content_area.set_margin_top(12)
        content_area.set_margin_bottom(12)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_area.pack_start(main_box, True, True, 0)

        # Header banner: logo, title, tagline and a tricolour accent line.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        logo_path = getattr(self, "icon_path", None)
        if logo_path:
            try:
                logo_pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(logo_path, 52, 52, True)
                header.pack_start(Gtk.Image.new_from_pixbuf(logo_pb), False, False, 0)
            except Exception as e:
                print("Settings logo note:", e)
        header_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header_text.set_valign(Gtk.Align.CENTER)
        lbl_title = Gtk.Label(label="Settings", xalign=0.0)
        lbl_title.get_style_context().add_class("settings-title")
        lbl_sub = Gtk.Label(label="Make Bharat Browser yours — fast, private, and proudly made in India", xalign=0.0)
        lbl_sub.get_style_context().add_class("settings-subtitle")
        header_text.pack_start(lbl_title, False, False, 0)
        header_text.pack_start(lbl_sub, False, False, 0)
        header.pack_start(header_text, True, True, 0)
        main_box.pack_start(header, False, False, 0)
        strip = Gtk.Box()
        strip.get_style_context().add_class("tricolor-strip")
        main_box.pack_start(strip, False, False, 0)

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
        search_bubble = Gtk.Label(label="🔍")
        search_bubble.get_style_context().add_class("settings-icon-bubble")
        search_bubble.set_valign(Gtk.Align.CENTER)
        search_row.pack_start(search_bubble, False, False, 0)
        search_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        s_title = Gtk.Label(xalign=0.0)
        s_title.set_markup("<span weight='semibold'>Default Search Engine</span>")
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
        home_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        home_bubble = Gtk.Label(label="🏠")
        home_bubble.get_style_context().add_class("settings-icon-bubble")
        home_bubble.set_valign(Gtk.Align.CENTER)
        home_head.pack_start(home_bubble, False, False, 0)
        home_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        h_title = Gtk.Label(xalign=0.0)
        h_title.set_markup("<span weight='semibold'>Custom Homepage</span>")
        h_title.get_style_context().add_class("settings-row-title")
        home_lbl_box.pack_start(h_title, False, False, 0)
        h_hint = Gtk.Label(xalign=0.0)
        h_hint.set_markup("<small>Loaded on new tabs (Ctrl+T) and initial startup.</small>")
        h_hint.get_style_context().add_class("settings-hint-label")
        home_lbl_box.pack_start(h_hint, False, False, 0)
        home_head.pack_start(home_lbl_box, True, True, 0)
        home_card.pack_start(home_head, False, False, 0)

        home_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        home_entry = Gtk.Entry()
        home_entry.set_text(self.homepage)
        home_entry.set_placeholder_text("e.g. example.com or https://example.com")
        home_entry.set_hexpand(True)
        home_box.pack_start(home_entry, True, True, 0)

        btn_set_home = Gtk.Button(label="Set")
        btn_set_home.get_style_context().add_class("settings-primary-btn")
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

        typing_card = self._create_setting_card("TYPING & READING")
        typing_card.pack_start(
            self._create_toggle_row(
                "✍️ Check spelling while typing",
                "Underlines misspelled words in text boxes, using the dictionaries installed on your system.",
                self.spellcheck_enabled,
                self.on_spellcheck_toggled
            ),
            False, False, 0
        )
        typing_card.pack_start(self._hint("Tip: press Ctrl+Alt+R on an article for a clean, distraction-free Reader mode."), False, False, 0)
        gen_box.pack_start(typing_card, False, False, 0)
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

        tracker_card = self._create_setting_card("TRACKER LISTS")
        tracker_card.pack_start(
            self._create_toggle_row(
                "🛰️ Keep tracker lists up to date",
                "Downloads the EasyPrivacy list from easylist.to about once a week and blocks those third-party trackers. "
                "Only whole-domain rules are used, and sign-in/captcha services are never blocked.",
                self.tracker_lists_enabled,
                self.on_tracker_lists_toggled
            ),
            False, False, 0
        )
        tracker_status = Gtk.Label(xalign=0.0)
        tracker_status.set_text(self._tracker_status_text())
        tracker_status.get_style_context().add_class("settings-hint-label")
        tracker_card.pack_start(tracker_status, False, False, 0)
        btn_tracker_update = Gtk.Button(label="🔄 Update tracker list now")
        btn_tracker_update.get_style_context().add_class("settings-action-btn")

        def _tracker_update_clicked(_btn):
            btn_tracker_update.set_sensitive(False)
            tracker_status.set_text("Downloading…")

            def done(error):
                btn_tracker_update.set_sensitive(True)
                tracker_status.set_text(f"❌ Update failed: {error}" if error else "✅ " + self._tracker_status_text())

            self.refresh_tracker_list_async(done)

        btn_tracker_update.connect("clicked", _tracker_update_clicked)
        tracker_card.pack_start(btn_tracker_update, False, False, 0)
        priv_box.pack_start(tracker_card, False, False, 0)

        sites_card = self._create_setting_card("SITES, PASSWORDS & REPORT")
        sites_card.pack_start(
            self._create_toggle_row(
                "🔑 Offer to save passwords",
                "Saved in your system keyring (GNOME Keyring, KWallet or KeePassXC), never in Bharat Browser's own files. "
                "Not used in private windows.",
                self.passwords_enabled,
                self.on_passwords_toggled
            ),
            False, False, 0
        )
        for label_text, handler in (
            ("🔑 Manage saved passwords…", lambda b: self.open_password_manager(dialog)),
            ("🌐 Manage site settings & permissions…", lambda b: self.open_site_settings_manager(dialog)),
            ("📊 Open my Privacy Report", lambda b: (dialog.destroy(), self.open_privacy_report())),
        ):
            site_btn = Gtk.Button(label=label_text)
            site_btn.get_style_context().add_class("settings-action-btn")
            site_btn.connect("clicked", handler)
            sites_card.pack_start(site_btn, False, False, 0)
        priv_box.pack_start(sites_card, False, False, 0)
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

        import_card = self._create_setting_card("IMPORT FROM OTHER BROWSERS")
        import_card.pack_start(self._hint("Bring your bookmarks and history from Firefox, Chrome, Chromium, Brave, Edge, "
                                          "Vivaldi or Opera, or from an exported bookmarks .html file."), False, False, 0)
        btn_import = Gtk.Button(label="📥 Import bookmarks & history…")
        btn_import.get_style_context().add_class("settings-primary-btn")
        btn_import.set_sensitive(not self.is_private)
        btn_import.connect("clicked", lambda b: self.open_import_dialog(dialog))
        import_card.pack_start(btn_import, False, False, 2)
        act_box.pack_start(import_card, False, False, 0)

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
            f"Engineered in INDIA "
            "<span foreground='#ff9933'>▰</span><span foreground='#e2e8f0'>▰</span><span foreground='#138808'>▰</span></small>"
        )
        about_lbl.get_style_context().add_class("settings-hint-label")
        about_card.pack_start(about_lbl, False, False, 0)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for chip_text, chip_class in (
            (f"v{self.current_version}", None),
            ("🔐 Signed updates", "settings-chip-green"),
            ("Made in India", "settings-chip-saffron"),
        ):
            chip = Gtk.Label(label=chip_text)
            chip.get_style_context().add_class("settings-chip")
            if chip_class:
                chip.get_style_context().add_class(chip_class)
            chips.pack_start(chip, False, False, 0)
        about_card.pack_start(chips, False, False, 2)

        about_card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        update_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        btn_check_update = Gtk.Button(label="🔄 Check for Updates on GitHub")
        btn_check_update.get_style_context().add_class("settings-primary-btn")
        update_vbox.pack_start(btn_check_update, False, False, 0)

        update_status_lbl = Gtk.Label(xalign=0.0)
        update_status_lbl.get_style_context().add_class("settings-hint-label")
        update_status_lbl.set_line_wrap(True)
        update_vbox.pack_start(update_status_lbl, False, False, 0)

        btn_restart_applied = Gtk.Button(label="🚀 Restart Now to Apply Update")
        btn_restart_applied.get_style_context().add_class("settings-primary-btn")
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

    def on_spellcheck_toggled(self, active):
        self.spellcheck_enabled = active
        try:
            self.context.set_spell_checking_enabled(active)
        except Exception as e:
            print("Spell-check toggle note:", e)
        self.save_settings()

    def on_tracker_lists_toggled(self, active):
        self.tracker_lists_enabled = active
        self.save_settings()
        self._compile_content_blocker_filter()  # recompile with or without the downloaded list
        if active and not self.is_private:
            self._maybe_refresh_tracker_list()

    def on_passwords_toggled(self, active):
        self.passwords_enabled = active
        self.save_settings()

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

    # ------------------------------------------------------------------
    # Per-site policy: ad blocking, JavaScript and zoom remembered per host
    # ------------------------------------------------------------------
    def _save_site_settings(self):
        if not self.is_private:  # private windows keep these in memory only
            save_site_settings(self.site_settings)

    def _site_wants_filter(self, host):
        return self.adblock_enabled and self.site_settings.get(host, {}).get("adblock") is not False

    def _settings_without_javascript(self):
        clone = WebKit2.Settings()
        for prop in self.web_settings.list_properties():
            if prop.flags & GObject.ParamFlags.WRITABLE and not prop.flags & GObject.ParamFlags.CONSTRUCT_ONLY:
                try:
                    clone.set_property(prop.name, self.web_settings.get_property(prop.name))
                except Exception:
                    pass
        clone.set_enable_javascript(False)
        return clone

    def _apply_site_policy(self, webview, host):
        """Make this webview's content blocker / JavaScript setting match the
        per-site choices for `host`. Called when a navigation starts, so it is in
        place before the new page's document is created."""
        entry = self.site_settings.get(host, {})
        want_filter = self._site_wants_filter(host)
        if self.content_filter is not None and want_filter != getattr(webview, "_bharat_filter_on", False):
            ucm = webview.get_user_content_manager()
            if want_filter:
                ucm.add_filter(self.content_filter)
            else:
                ucm.remove_filter(self.content_filter)
            webview._bharat_filter_on = want_filter
        js_off = entry.get("javascript") is False
        if js_off != getattr(webview, "_bharat_js_off", False):
            webview.set_settings(self._settings_without_javascript() if js_off else self.web_settings)
            webview._bharat_js_off = js_off

    def _remember_zoom(self, webview):
        host = site_host_of(webview.get_uri())
        if not host:
            return
        level = round(webview.get_zoom_level(), 2)
        set_site_value(self.site_settings, host, "zoom", None if abs(level - 1.0) < 0.01 else level)
        self._save_site_settings()

    def _apply_saved_zoom(self, webview):
        host = site_host_of(webview.get_uri())
        if not host:
            return
        level = self.site_settings.get(host, {}).get("zoom", 1.0)
        if isinstance(level, (int, float)) and abs(webview.get_zoom_level() - level) > 0.01:
            webview.set_zoom_level(max(self.ZOOM_MIN, min(self.ZOOM_MAX, level)))

    # ------------------------------------------------------------------
    # Permissions (camera/mic, location, notifications) with "remember"
    # ------------------------------------------------------------------
    @staticmethod
    def _permission_kind(request):
        if isinstance(request, WebKit2.UserMediaPermissionRequest):
            return "media"
        if isinstance(request, WebKit2.GeolocationPermissionRequest):
            return "location"
        if isinstance(request, WebKit2.NotificationPermissionRequest):
            return "notifications"
        return None

    def on_permission_request(self, webview, request):
        host = site_host_of(webview.get_uri())
        kind = self._permission_kind(request)
        remembered = get_site_permission(self.site_settings, host, kind) if kind and host else None
        if remembered == "allow":
            request.allow()
            return True
        if remembered == "deny":
            request.deny()
            return True

        what = {"media": "camera/microphone access", "location": "your location",
                "notifications": "notifications"}.get(kind, "a permission")
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Allow {what}?"
        )
        dialog.get_style_context().add_class("bharat-dialog")
        dialog.format_secondary_text(webview.get_uri() or "This site")
        remember_chk = None
        if kind and host:
            remember_chk = Gtk.CheckButton(label=f"Remember my choice for {host}")
            dialog.get_message_area().pack_start(remember_chk, False, False, 6)
            remember_chk.show()
        response = dialog.run()
        remember = bool(remember_chk and remember_chk.get_active())
        dialog.destroy()
        allowed = response == Gtk.ResponseType.YES
        if remember:
            set_site_value(self.site_settings, host, "permissions", "allow" if allowed else "deny", sub=kind)
            self._save_site_settings()
        request.allow() if allowed else request.deny()
        return True

    # ------------------------------------------------------------------
    # Privacy statistics + report page
    # ------------------------------------------------------------------
    def _count_event(self, key):
        self.stats[key] = self.stats.get(key, 0) + 1
        self.session_stats[key] = self.session_stats.get(key, 0) + 1

    def _count_blocked(self, webview, uri):
        self._count_event("blocked")
        self.blocked_domains[site_host_of(uri)] += 1
        self.blocked_sites[site_host_of(webview.get_uri())] += 1
        if not self.is_private:
            self._schedule_session_save()  # stats ride along with the debounced session write

    def build_privacy_report_html(self):
        esc = html_module.escape
        since = time.strftime("%d %b %Y", time.localtime(self.stats.get("since", time.time())))

        def card(label, value, sub=""):
            return (f"<div class='card'><div class='n'>{value:,}</div><div class='l'>{esc(label)}</div>"
                    f"<div class='s'>{esc(sub)}</div></div>")

        def table(title, counter, empty):
            rows = "".join(
                f"<tr><td>{esc(host or '(unknown)')}</td><td class='c'>{count:,}</td>"
                f"<td class='b'><span style='width:{max(4, int(100 * count / max(counter.values())))}%'></span></td></tr>"
                for host, count in counter.most_common(10))
            body = f"<table>{rows}</table>" if rows else f"<p class='e'>{esc(empty)}</p>"
            return f"<section><h2>{esc(title)}</h2>{body}</section>"

        s, t = self.session_stats, self.stats
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Privacy Report</title><style>
body{{margin:0;background:#0b0e14;color:#f8fafc;font-family:system-ui,sans-serif;padding:0 20px}}
.flag{{position:fixed;top:0;left:0;right:0;height:5px;background:linear-gradient(90deg,#ff9933 33%,#fff 33% 66%,#138808 66%)}}
.wrap{{max-width:760px;margin:0 auto;padding:44px 0 60px}}h1{{margin:0 0 4px}}.sub{{color:#94a3b8;margin-bottom:26px}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:10px}}
.card{{background:#11151d;border:1px solid #243049;border-radius:14px;padding:18px;border-left:3px solid #6366f1}}
.n{{font-size:30px;font-weight:800}}.l{{color:#cbd5e1;margin-top:2px}}.s{{color:#64748b;font-size:12px;margin-top:6px}}
h2{{font-size:15px;color:#a5b4fc;letter-spacing:.5px;margin:30px 0 8px}}
table{{width:100%;border-collapse:collapse}}td{{padding:7px 4px;border-bottom:1px solid #1e293b;font-size:14px}}
td.c{{text-align:right;color:#cbd5e1;width:70px}}td.b{{width:30%}}td.b span{{display:block;height:8px;border-radius:99px;background:linear-gradient(90deg,#6366f1,#8b5cf6)}}
.e{{color:#64748b}}.note{{color:#64748b;font-size:12px;margin-top:30px;line-height:1.6}}
@media(max-width:560px){{.cards{{grid-template-columns:1fr}}}}
</style></head><body><div class="flag"></div><div class="wrap">
<h1>🛡️ Privacy Report</h1><div class="sub">What Bharat Browser protected you from. Totals since {esc(since)}.</div>
<div class="cards">{card("Trackers & ads blocked", t.get("blocked", 0), f"{s.get('blocked', 0):,} this session")}
{card("Tracking parameters removed", t.get("params", 0), f"{s.get('params', 0):,} this session")}
{card("Connections upgraded to HTTPS", t.get("https", 0), f"{s.get('https', 0):,} this session")}</div>
{table("Most blocked tracker domains (this session)", self.blocked_domains, "Nothing blocked yet this session.")}
{table("Sites with the most blocked requests (this session)", self.blocked_sites, "Nothing blocked yet this session.")}
<div class="note">Counts cover requests blocked by the browser's built-in shield. The native content blocker and downloaded tracker lists also work silently, so real protection is higher than shown. {"Private windows don't save any of this." if self.is_private else ""}</div>
</div></body></html>"""

    def open_privacy_report(self):
        webview = self.create_new_tab(url="about:blank")
        html = self.build_privacy_report_html()
        GLib.idle_add(lambda: webview.load_html(html, None))

    # ------------------------------------------------------------------
    # HTTPS-only warning page + the bharat:// scheme it uses
    # ------------------------------------------------------------------
    def _on_bharat_scheme(self, request, user_data):
        """Serves bharat:// links created by our own pages. allow-http only works
        with a one-time token minted by the warning page, so a website can't use
        it to switch off HTTPS upgrading for a host of its choosing."""
        parsed = urllib.parse.urlparse(request.get_uri())
        page = "<html><body style='background:#0b0e14;color:#cbd5e1;font-family:sans-serif;padding:40px'>Nothing here.</body></html>"
        if parsed.netloc == "allow-http":
            token = (urllib.parse.parse_qs(parsed.query).get("t") or [""])[0]
            target = self._http_tokens.pop(token, "")
            host = site_host_of(target)
            if target.startswith("http://") and host:
                self._http_allowed_hosts.add(host)
                page = f"<html><head><meta http-equiv='refresh' content='0;url={html_module.escape(target, quote=True)}'></head></html>"
        data = page.encode("utf-8")
        request.finish(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data)), len(data), "text/html")

    def _recently_upgraded_to_https(self, host):
        return time.monotonic() - self._recent_https_upgrades.get(host, -1e9) < 30

    def _show_https_warning(self, webview, failing_uri, error_message):
        host = site_host_of(failing_uri)
        http_uri = "http://" + failing_uri[len("https://"):]
        token = secrets_module.token_urlsafe(12)
        self._http_tokens[token] = http_uri
        if len(self._http_tokens) > 50:
            self._http_tokens.pop(next(iter(self._http_tokens)))
        esc = html_module.escape
        body = (f"<p>Bharat Browser tried a secure (HTTPS) connection to <b>{esc(host)}</b> but couldn't connect. "
                "The site may be down, or it may not support HTTPS.</p>"
                "<p style='color:#fca5a5'>If you continue over plain HTTP, anyone on your network can read or change "
                "what you send and receive. Don't enter passwords or personal details.</p>")
        actions = (f'<a class="btn" href="{esc(failing_uri, quote=True)}">Try again</a>'
                   f'<a class="btn alt" href="bharat://allow-http?t={token}">Continue to HTTP (not secure)</a>')
        page = build_notice_page("🔓", "Secure connection unavailable", body, actions, error_message)
        GLib.idle_add(lambda: webview.load_html(page, failing_uri))
        self.statusbar.push(self.context_id, f"🔓 {host} didn't answer over HTTPS")

    # ------------------------------------------------------------------
    # Tabs: pinning, context menu, reopen closed tab
    # ------------------------------------------------------------------
    def reopen_closed_tab(self):
        if not self._closed_tabs:
            self.statusbar.push(self.context_id, "No recently closed tabs")
            return
        entry = self._closed_tabs.pop()
        self.create_new_tab(entry["url"])

    def _pinned_count(self):
        return sum(1 for i in range(self.notebook.get_n_pages())
                   if getattr(self.notebook.get_nth_page(i), "_bharat_pinned", False))

    def set_tab_pinned(self, tab_box, pinned):
        if bool(getattr(tab_box, "_bharat_pinned", False)) == bool(pinned):
            return
        tab_box._bharat_pinned = bool(pinned)
        tab_box._bharat_close_btn.set_visible(not pinned)
        tab_box._bharat_label.set_width_chars(7 if pinned else 12)
        tab_box._bharat_label.set_max_width_chars(9 if pinned else 18)
        webview = tab_box._bharat_webview
        self._apply_display_title(webview)
        count = sum(1 for i in range(self.notebook.get_n_pages())
                    if getattr(self.notebook.get_nth_page(i), "_bharat_pinned", False)
                    and self.notebook.get_nth_page(i) is not tab_box)
        self.notebook.reorder_child(tab_box, count)
        self._schedule_session_save()

    def close_other_tabs(self, keep_box):
        for i in reversed(range(self.notebook.get_n_pages())):
            tb = self.notebook.get_nth_page(i)
            if tb is not keep_box and not getattr(tb, "_bharat_pinned", False):
                self.close_tab(tb)

    def close_tabs_to_right(self, tab_box):
        start = self.notebook.page_num(tab_box) + 1
        for i in reversed(range(start, self.notebook.get_n_pages())):
            tb = self.notebook.get_nth_page(i)
            if not getattr(tb, "_bharat_pinned", False):
                self.close_tab(tb)

    def duplicate_tab(self, tab_box):
        uri = self._suspended_tab_uris.get(id(tab_box)) or tab_box._bharat_webview.get_uri()
        if uri and not uri.startswith("about:"):
            self.create_new_tab(uri)

    def _on_tab_header_click(self, tab_box, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.button == 2:  # middle-click closes, like most browsers
            if not getattr(tab_box, "_bharat_pinned", False):
                self.close_tab(tab_box)
            return True
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        pinned = getattr(tab_box, "_bharat_pinned", False)
        entries = [
            ("Reload", lambda: tab_box._bharat_webview.reload()),
            ("Duplicate Tab", lambda: self.duplicate_tab(tab_box)),
            ("Unpin Tab" if pinned else "Pin Tab", lambda: self.set_tab_pinned(tab_box, not pinned)),
            None,
            ("Close Tab", lambda: self.close_tab(tab_box)),
            ("Close Other Tabs", lambda: self.close_other_tabs(tab_box)),
            ("Close Tabs to the Right", lambda: self.close_tabs_to_right(tab_box)),
            None,
            ("Reopen Closed Tab", self.reopen_closed_tab),
        ]
        for entry in entries:
            if entry is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=entry[0])
            item.connect("activate", lambda _i, fn=entry[1]: fn())
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    # ------------------------------------------------------------------
    # Reader mode
    # ------------------------------------------------------------------
    def toggle_reader_mode(self):
        webview = self.get_active_webview()
        uri = (webview.get_uri() or "") if webview else ""
        if not uri.startswith(("http://", "https://", "file://")):
            self.statusbar.push(self.context_id, "📖 Reader mode works on web pages")
            return
        webview.run_javascript(READER_MODE_JS, None, self._on_reader_result, None)

    def _on_reader_result(self, webview, result, user_data):
        try:
            value = webview.run_javascript_finish(result).get_js_value().to_string()
        except Exception as e:
            self.statusbar.push(self.context_id, f"📖 Reader mode unavailable on this page ({e})")
            return
        if value == "no-article":
            self.statusbar.push(self.context_id, "📖 No readable article found on this page")
        elif value == "opened":
            self.statusbar.push(self.context_id, "📖 Reader mode on — press Esc to close")

    # ------------------------------------------------------------------
    # Main menu (the ☰ button) and the per-site popover (lock icon)
    # ------------------------------------------------------------------
    def show_main_menu(self, button):
        popover = Gtk.Popover.new(button)
        popover.get_style_context().add_class("bharat-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8); box.set_margin_end(8); box.set_margin_top(8); box.set_margin_bottom(8)

        def item(label, fn, enabled=True):
            b = Gtk.ModelButton()
            b.set_property("text", label)
            b.set_sensitive(enabled)
            b.connect("clicked", lambda _b: (popover.popdown(), GLib.idle_add(lambda: (fn(), False)[1])))
            box.pack_start(b, False, False, 0)

        def sep():
            box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        item("New Tab  (Ctrl+T)", lambda: self.create_new_tab(self.homepage))
        item("New Private Window  (Ctrl+Shift+N)", self.open_private_window)
        item("Reopen Closed Tab  (Ctrl+Shift+T)", self.reopen_closed_tab, bool(self._closed_tabs))
        sep()
        item("Reader Mode  (Ctrl+Alt+R)", self.toggle_reader_mode)
        item("Print…  (Ctrl+P)", self.print_active_page)
        item("Find in Page  (Ctrl+F)", self.open_find_bar)
        zoom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        zoom_row.pack_start(Gtk.Label(label="Zoom", xalign=0.0), True, True, 8)
        for text, fn in (("−", lambda: self.adjust_zoom(-0.1)), ("100%", lambda: self.adjust_zoom(reset=True)),
                         ("+", lambda: self.adjust_zoom(0.1))):
            zb = Gtk.Button(label=text)
            zb.get_style_context().add_class("settings-action-btn")
            zb.connect("clicked", lambda _b, fn=fn: fn())
            zoom_row.pack_start(zb, False, False, 0)
        box.pack_start(zoom_row, False, False, 4)
        sep()
        item("Bookmarks  (Ctrl+Shift+O)", self.open_bookmark_manager)
        item("History  (Ctrl+H)", self.open_history_tab)
        item("Downloads", lambda: self.on_downloads_clicked(None))
        item("Privacy Report", self.open_privacy_report)
        sep()
        item("Settings", lambda: self.on_settings_clicked(None))
        popover.add(box)
        box.show_all()
        popover.popup()

    def show_site_popover(self):
        webview = self.get_active_webview()
        uri = (webview.get_uri() or "") if webview else ""
        host = site_host_of(uri)
        if not host:
            return
        popover = Gtk.Popover.new(self.url_entry)
        rect = self.url_entry.get_icon_area(Gtk.EntryIconPosition.PRIMARY)
        popover.set_pointing_to(rect)
        popover.get_style_context().add_class("bharat-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for setter in (box.set_margin_start, box.set_margin_end, box.set_margin_top, box.set_margin_bottom):
            setter(14)

        secure = uri.startswith("https://")
        head = Gtk.Label(xalign=0.0)
        head.set_markup(f"<b>{GLib.markup_escape_text(host)}</b>\n<small>"
                        + ("🔒 Secure connection (HTTPS)" if secure else "⚠️ Not secure (HTTP)") + "</small>")
        box.pack_start(head, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
        entry = self.site_settings.get(host, {})

        def switch_row(text, active, on_change):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.pack_start(Gtk.Label(label=text, xalign=0.0), True, True, 0)
            sw = Gtk.Switch()
            sw.set_active(active)
            sw.connect("notify::active", lambda s, _p: on_change(s.get_active()))
            row.pack_end(sw, False, False, 0)
            box.pack_start(row, False, False, 0)

        def change(key, value, reload=True):
            set_site_value(self.site_settings, host, key, value)
            self._save_site_settings()
            self._apply_site_policy(webview, host)
            if reload:
                webview.reload()

        switch_row("Block ads & trackers", entry.get("adblock") is not False,
                   lambda on: change("adblock", None if on else False))
        switch_row("Allow JavaScript", entry.get("javascript") is not False,
                   lambda on: change("javascript", None if on else False))
        if not self.is_private:
            switch_row("Offer to save passwords", entry.get("passwords") is not False,
                       lambda on: change("passwords", None if on else False, reload=False))
        zoom = round(webview.get_zoom_level() * 100)
        zrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zrow.pack_start(Gtk.Label(label=f"Zoom: {zoom}% (remembered for this site)", xalign=0.0), True, True, 0)
        zreset = Gtk.Button(label="Reset")
        zreset.get_style_context().add_class("settings-action-btn")
        zreset.connect("clicked", lambda _b: (self.adjust_zoom(reset=True), popover.popdown()))
        zrow.pack_end(zreset, False, False, 0)
        box.pack_start(zrow, False, False, 0)

        perms = entry.get("permissions") or {}
        for kind, label in PERMISSION_KINDS.items():
            if kind in perms:
                prow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                prow.pack_start(Gtk.Label(label=f"{label}: {'Allowed' if perms[kind] == 'allow' else 'Blocked'}", xalign=0.0), True, True, 0)
                pbtn = Gtk.Button(label="Reset")
                pbtn.get_style_context().add_class("settings-action-btn")
                pbtn.connect("clicked", lambda _b, k=kind: (set_site_value(self.site_settings, host, "permissions", None, sub=k),
                                                            self._save_site_settings(), popover.popdown()))
                prow.pack_end(pbtn, False, False, 0)
                box.pack_start(prow, False, False, 0)

        reader = Gtk.Button(label="📖 Reader mode")
        reader.get_style_context().add_class("settings-action-btn")
        reader.connect("clicked", lambda _b: (popover.popdown(), self.toggle_reader_mode()))
        box.pack_start(reader, False, False, 2)
        popover.add(box)
        box.show_all()
        popover.popup()

    # ------------------------------------------------------------------
    # Small shared UI helpers
    # ------------------------------------------------------------------
    def _make_dialog(self, title, parent=None, width=520, height=0):
        dialog = Gtk.Dialog(title=title, transient_for=parent or self, modal=True, destroy_with_parent=True)
        dialog.get_style_context().add_class("bharat-dialog")
        self.apply_dark_titlebar(dialog, title)
        close_btn = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        close_btn.get_style_context().add_class("settings-primary-btn")
        dialog.set_default_size(width, height)
        area = dialog.get_content_area()
        for setter in (area.set_margin_start, area.set_margin_end, area.set_margin_top, area.set_margin_bottom):
            setter(16)
        area.set_spacing(10)
        return dialog, area

    @staticmethod
    def _hint(text):
        label = Gtk.Label(xalign=0.0)
        label.set_markup(f"<small>{GLib.markup_escape_text(text)}</small>")
        label.get_style_context().add_class("settings-hint-label")
        label.set_line_wrap(True)
        return label

    def _clear_infobar(self, bar=None):
        if bar is not None and bar is not self._infobar:
            return False
        if self._infobar is not None:
            self._infobar.destroy()
            self._infobar = None
        return False

    def _show_infobar(self, message, buttons, extra=None, timeout=60):
        """Slim bar under the toolbar. `buttons` = [(label, callback)]; it is dismissed
        by any button, its close ✕, or after `timeout` seconds."""
        self._clear_infobar()
        bar = Gtk.InfoBar()
        bar.get_style_context().add_class("bharat-infobar")
        bar.set_show_close_button(True)
        content = bar.get_content_area()
        label = Gtk.Label(label=message, xalign=0.0)
        label.set_line_wrap(True)
        content.pack_start(label, True, True, 0)
        if extra is not None:
            content.pack_start(extra, False, False, 6)
        callbacks = {}
        for index, (text, callback) in enumerate(buttons, start=1):
            button = bar.add_button(text, index)
            button.get_style_context().add_class("settings-action-btn")
            callbacks[index] = callback

        def on_response(_bar, response):
            callback = callbacks.get(response)
            self._clear_infobar(bar)
            if callback:
                callback()

        bar.connect("response", on_response)
        self.infobar_box.pack_start(bar, False, False, 0)
        bar.show_all()
        self._infobar = bar
        GLib.timeout_add_seconds(timeout, self._clear_infobar, bar)

    # ------------------------------------------------------------------
    # Import bookmarks / history
    # ------------------------------------------------------------------
    def _refill_autocomplete(self):
        store = self.url_completion_store
        store.clear()
        for entry in self.url_history:
            store.append([entry["url"], entry.get("title") or entry["url"]])

    def _apply_import(self, bookmarks, history):
        """Merge imported items into the live lists and save. Returns (new_bookmarks, new_history)."""
        self.bookmarks, added_b = merge_bookmarks(self.bookmarks, bookmarks)
        self.url_history, added_h = merge_history(self.url_history, history)
        if added_b:
            save_bookmarks(self.bookmarks)
        if added_h:
            save_url_history(self.url_history)
            self._refill_autocomplete()
        self.update_bookmark_star((self.get_active_webview().get_uri() or "") if self.get_active_webview() else "")
        return added_b, added_h

    def open_import_dialog(self, parent=None):
        if self.is_private:
            self.statusbar.push(self.context_id, "Import isn't available in private windows")
            return
        dialog, area = self._make_dialog("Import from another browser", parent, 540)
        profiles = find_importable_profiles()
        area.pack_start(self._hint("Reads bookmarks and history from the other browser's files on this computer. "
                                   "Nothing is changed or deleted there, and nothing is sent anywhere."), False, False, 0)
        status = Gtk.Label(xalign=0.0)
        status.set_line_wrap(True)
        status.get_style_context().add_class("settings-hint-label")

        combo = Gtk.ComboBoxText()
        chk_bookmarks = Gtk.CheckButton(label="Bookmarks")
        chk_bookmarks.set_active(True)
        chk_history = Gtk.CheckButton(label="Browsing history")
        chk_history.set_active(True)
        btn_import = Gtk.Button(label="📥 Import")
        btn_import.get_style_context().add_class("settings-primary-btn")
        if profiles:
            for p in profiles:
                combo.append_text(f"{p['browser']} — {p['profile']}")
            combo.set_active(0)
            area.pack_start(combo, False, False, 0)
            area.pack_start(chk_bookmarks, False, False, 0)
            area.pack_start(chk_history, False, False, 0)
            area.pack_start(btn_import, False, False, 4)
        else:
            area.pack_start(Gtk.Label(label="No Firefox, Chrome, Chromium, Brave, Edge, Vivaldi or Opera profile found.", xalign=0.0), False, False, 0)

        def finish(added_b, added_h, errors, source):
            btn_import.set_sensitive(True)
            lines = [f"✅ Imported from {source}: {added_b} new bookmarks, {added_h} new history entries."]
            if errors:
                lines.append("⚠️ Some data couldn't be read: " + "; ".join(errors))
            status.set_text("\n".join(lines))
            return False

        def do_import(_btn):
            profile = profiles[combo.get_active()]
            want_b, want_h = chk_bookmarks.get_active(), chk_history.get_active()
            btn_import.set_sensitive(False)
            status.set_text("Importing…")

            def worker():
                bookmarks, history, errors = read_profile(profile, want_b, want_h)

                def apply():
                    added_b, added_h = self._apply_import(bookmarks, history)
                    return finish(added_b, added_h, errors, profile["browser"])

                GLib.idle_add(apply)

            threading.Thread(target=worker, daemon=True).start()

        btn_import.connect("clicked", do_import)

        file_btn = Gtk.Button(label="📄 Import a bookmarks file (.html)…")
        file_btn.get_style_context().add_class("settings-action-btn")

        def pick_file(_btn):
            chooser = Gtk.FileChooserDialog(title="Choose a bookmarks HTML file", transient_for=dialog,
                                            action=Gtk.FileChooserAction.OPEN)
            chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
            chooser.get_style_context().add_class("bharat-dialog")
            if chooser.run() == Gtk.ResponseType.OK:
                path = chooser.get_filename()
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        marks = parse_netscape_bookmarks(f.read())
                    added_b, _ = self._apply_import(marks, [])
                    status.set_text(f"✅ Imported {added_b} new bookmarks from {os.path.basename(path)}.")
                except Exception as e:
                    status.set_text(f"❌ Couldn't read that file: {e}")
            chooser.destroy()

        file_btn.connect("clicked", pick_file)
        area.pack_start(file_btn, False, False, 0)
        area.pack_start(status, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    # ------------------------------------------------------------------
    # Saved passwords (system keyring)
    # ------------------------------------------------------------------
    def _setup_password_detection(self, webview):
        """Hooks login-form detection into this webview's content manager (once per
        manager, since popups share their opener's)."""
        if self.is_private or self.password_script is None:
            return
        ucm = webview.get_user_content_manager()
        if getattr(ucm, "_bharat_pw_ready", False):
            return
        ucm._bharat_pw_ready = True
        ucm.add_script(self.password_script)
        if ucm.register_script_message_handler_in_world("bharatPw", "bharat-pw"):
            handler = ucm.connect("script-message-received::bharatPw", self._on_password_message)
            webview._bharat_sig_ids = getattr(webview, "_bharat_sig_ids", []) + [(ucm, handler)]

    def _on_password_message(self, ucm, js_result):
        if self.is_private or not self.passwords_enabled:
            return
        try:
            message = json.loads(js_result.get_js_value().to_json(0))
            kind = message.get("type")
        except Exception:
            return
        webview = self.get_active_webview()
        uri = (webview.get_uri() or "") if webview else ""
        host = site_host_of(uri)
        if not host or self.site_settings.get(host, {}).get("passwords") is False:
            return
        if kind == "submit":
            user, password = message.get("user", ""), message.get("pass", "")
            if isinstance(user, str) and isinstance(password, str) and 0 < len(password) <= 1024 and len(user) <= 512:
                self._offer_save_password(host, user, password)
        elif kind == "form" and (uri.startswith("https://") or is_local_network_host(host)):
            self._offer_fill_password(webview, host, uri)

    def _offer_save_password(self, host, user, password):
        if not self.secrets.available():
            self.statusbar.push(self.context_id, "🔑 No system keyring found, so passwords can't be saved")
            return
        existing = [f for f in self.secrets.find(host) if f["username"] == user]
        if existing:
            if self.secrets.get_password(existing[0]["item"]) == password:
                return
            question, verb = f"Update the saved password for {user or 'this login'} on {host}?", "Update"
        else:
            question, verb = f"Save the password for {user or 'this login'} on {host}?", "Save"

        def save():
            ok = self.secrets.store(host, user, password)
            self.statusbar.push(self.context_id, "🔑 Password saved to your keyring" if ok else "❌ Couldn't save the password")

        def never():
            set_site_value(self.site_settings, host, "passwords", False)
            self._save_site_settings()
            self.statusbar.push(self.context_id, f"🔑 Won't offer to save passwords on {host}")

        self._show_infobar("🔑 " + question, [(verb, save), ("Never for this site", never), ("Not now", None)])

    def _offer_fill_password(self, webview, host, uri):
        key = (id(webview), uri)
        if key in self._fill_offered:
            return
        self._fill_offered.add(key)
        logins = self.secrets.find(host) if self.secrets.available() else []
        if not logins:
            return
        combo = None
        if len(logins) > 1:
            combo = Gtk.ComboBoxText()
            for login in logins:
                combo.append_text(login["username"] or "(no username)")
            combo.set_active(0)

        def fill():
            login = logins[combo.get_active() if combo else 0]
            password = self.secrets.get_password(login["item"])
            if password is None:
                self.statusbar.push(self.context_id, "❌ Couldn't read the saved password")
                return
            script = PASSWORD_FILL_JS % (json.dumps(login["username"]), json.dumps(password))
            webview.run_javascript(script, None, None, None)

        who = logins[0]["username"] or "your saved login"
        self._show_infobar(f"🔑 Fill {who if len(logins) == 1 else 'a saved login'} for {host}?",
                           [("Fill", fill), ("Not now", None)], extra=combo)

    def open_password_manager(self, parent=None):
        dialog, area = self._make_dialog("Saved passwords", parent, 560, 420)
        area.pack_start(self._hint("Passwords are stored in your system keyring (GNOME Keyring, KWallet or KeePassXC), "
                                   "not in Bharat Browser's own files."), False, False, 0)
        if not self.secrets.available():
            area.pack_start(Gtk.Label(label="No keyring service was found, so password saving is unavailable.\n"
                                            "Install and unlock GNOME Keyring, KWallet or KeePassXC (Secret Service).",
                                      xalign=0.0), False, False, 0)
            dialog.show_all(); dialog.run(); dialog.destroy()
            return
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroller.add(listbox)
        area.pack_start(scroller, True, True, 0)

        def refresh():
            for child in listbox.get_children():
                child.destroy()
            logins = self.secrets.find()
            if not logins:
                listbox.pack_start(Gtk.Label(label="No saved passwords yet.", xalign=0.0), False, False, 0)
            for login in logins:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                label = Gtk.Label(xalign=0.0)
                label.set_markup(f"<b>{GLib.markup_escape_text(login['host'])}</b>\n<small>{GLib.markup_escape_text(login['username'] or '(no username)')}</small>")
                row.pack_start(label, True, True, 0)
                copy_btn = Gtk.Button(label="Copy password")
                copy_btn.get_style_context().add_class("settings-action-btn")
                copy_btn.connect("clicked", lambda _b, item=login["item"]: self._copy_password(item))
                del_btn = Gtk.Button(label="Delete")
                del_btn.get_style_context().add_class("settings-danger-btn")
                del_btn.connect("clicked", lambda _b, item=login["item"]: (self.secrets.delete(item), refresh()))
                row.pack_end(del_btn, False, False, 0)
                row.pack_end(copy_btn, False, False, 0)
                listbox.pack_start(row, False, False, 0)
            listbox.show_all()

        refresh()
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _copy_password(self, item):
        password = self.secrets.get_password(item)
        if password is None:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(password, -1)
        self.statusbar.push(self.context_id, "🔑 Password copied — the clipboard clears in 30 seconds")

        def clear():
            if clipboard.wait_for_text() == password:
                clipboard.clear()
            return False

        GLib.timeout_add_seconds(30, clear)

    # ------------------------------------------------------------------
    # Per-site settings manager
    # ------------------------------------------------------------------
    @staticmethod
    def _describe_site_entry(entry):
        parts = []
        for kind, value in (entry.get("permissions") or {}).items():
            parts.append(f"{PERMISSION_KINDS.get(kind, kind)}: {'allowed' if value == 'allow' else 'blocked'}")
        if isinstance(entry.get("zoom"), (int, float)):
            parts.append(f"zoom {round(entry['zoom'] * 100)}%")
        if entry.get("adblock") is False:
            parts.append("ad blocking off")
        if entry.get("javascript") is False:
            parts.append("JavaScript off")
        if entry.get("passwords") is False:
            parts.append("no password prompts")
        return ", ".join(parts) or "default settings"

    def open_site_settings_manager(self, parent=None):
        dialog, area = self._make_dialog("Site settings", parent, 560, 420)
        area.pack_start(self._hint("Choices you made for individual sites: permissions, zoom, ad blocking, JavaScript. "
                                   "Change them from the 🔒 icon in the address bar."
                                   + (" Private windows keep these in memory only." if self.is_private else "")), False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroller.add(listbox)
        area.pack_start(scroller, True, True, 0)
        reset_all = Gtk.Button(label="Reset all sites")
        reset_all.get_style_context().add_class("settings-danger-btn")
        area.pack_start(reset_all, False, False, 0)

        def refresh():
            for child in listbox.get_children():
                child.destroy()
            if not self.site_settings:
                listbox.pack_start(Gtk.Label(label="No site-specific settings yet.", xalign=0.0), False, False, 0)
            for host in sorted(self.site_settings):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                label = Gtk.Label(xalign=0.0)
                label.set_line_wrap(True)
                label.set_markup(f"<b>{GLib.markup_escape_text(host)}</b>\n<small>{GLib.markup_escape_text(self._describe_site_entry(self.site_settings[host]))}</small>")
                row.pack_start(label, True, True, 0)
                btn = Gtk.Button(label="Reset")
                btn.get_style_context().add_class("settings-action-btn")
                btn.connect("clicked", lambda _b, h=host: (self.site_settings.pop(h, None), self._save_site_settings(), refresh()))
                row.pack_end(btn, False, False, 0)
                listbox.pack_start(row, False, False, 0)
            reset_all.set_sensitive(bool(self.site_settings))
            listbox.show_all()

        reset_all.connect("clicked", lambda _b: (self.site_settings.clear(), self._save_site_settings(), refresh()))
        refresh()
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    # ------------------------------------------------------------------
    # Tracker list updates
    # ------------------------------------------------------------------
    def _tracker_status_text(self):
        domains, fetched = load_tracker_list_cache()
        if not domains:
            return "Not downloaded yet."
        days = int((time.time() - fetched) // 86400)
        age = "today" if days <= 0 else f"{days} day{'s' if days != 1 else ''} ago"
        return f"{len(domains):,} tracker domains • updated {age}"

    def _maybe_refresh_tracker_list(self):
        if self.tracker_lists_enabled and not self.is_private:
            _, fetched = load_tracker_list_cache()
            if time.time() - fetched > TRACKER_LIST_MAX_AGE:
                self.refresh_tracker_list_async()
        return False

    def refresh_tracker_list_async(self, on_done=None):
        """Download the tracker list in a background thread, then recompile the blocker."""
        def worker():
            try:
                domains = fetch_tracker_list()
                error = None
            except Exception as e:
                domains, error = None, str(e)

            def finish():
                if domains is not None:
                    self._compile_content_blocker_filter()
                if on_done:
                    on_done(error)
                return False

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

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
