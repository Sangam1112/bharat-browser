#!/usr/bin/env python3
"""Integration tests that drive the real browser window (needs a display).
They use a throw-away HOME and local HTTP servers, never the network or your profile.
Run: python3 -m unittest discover -s tests -v"""
import http.server
import importlib.util
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

# Must be set before the browser module computes its config paths at import time.
_HOME = tempfile.mkdtemp(prefix="bharat-test-home-")
os.environ["HOME"] = _HOME

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("bb_window", os.path.join(ROOT, "bharat_browser.py"))
bb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bb)
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

HAVE_DISPLAY = Gtk.init_check()[0] if isinstance(Gtk.init_check(), tuple) else bool(Gtk.init_check())

ARTICLE = ("<html><head><title>Test Article</title><meta name='author' content='A. Writer'></head><body>"
           "<nav><a href='/'>Home</a> <a href='/menu'>Menu</a></nav><div class='content'><h1>The Great Test</h1>"
           + "".join(f"<p>Paragraph {i}: the quick brown fox jumps over the lazy dog while the reader mode "
                     f"extractor decides what counts as the real article text on this page.</p>" for i in range(6))
           + "<div class='share'><a href='/s'>Share this</a></div></div><footer>Copyright</footer></body></html>")
PAGES = {
    "/blank": "<html><title>blank</title></html>",
    "/article": ARTICLE,
    "/thin": "<html><title>thin</title><body><p>short</p></body></html>",
    "/login": ("<html><title>login</title><body><form action='/done' method='get'>"
               "<input id='u' type='text' name='user'><input id='p' type='password' name='pw'>"
               "<button id='go' type='submit'>Sign in</button></form></body></html>"),
    "/done": "<html><title>done</title><body>ok</body></html>",
    "/js": "<html><head><title>js-off</title></head><body><script>document.title='js-ran'</script></body></html>",
    "/third": "<html><title>third</title><body><img src='http://localhost:%PORT%/pixel'></body></html>",
}


class Handler(http.server.BaseHTTPRequestHandler):
    hits = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        Handler.hits[path] = Handler.hits.get(path, 0) + 1
        if path == "/pixel":
            body, ctype = b"\x89PNG", "image/png"
        else:
            body = PAGES.get(path, "<html><title>404</title></html>").replace("%PORT%", str(self.server.server_port)).encode()
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def spin(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def js(webview, script, timeout=5.0):
    box = {}

    def done(wv, result, _):
        try:
            box["v"] = wv.run_javascript_finish(result).get_js_value().to_string()
        except Exception as e:
            box["v"] = f"ERR {e}"

    webview.run_javascript(script, None, done, None)
    spin(lambda: "v" in box, timeout)
    return box.get("v")


class FakeSecrets:
    def __init__(self):
        self.items = {}
        self.error = ""

    def available(self):
        return True

    def store(self, host, user, password):
        self.items[(host, user)] = password
        return True

    def find(self, host=None):
        return [{"item": f"{h}|{u}", "host": h, "username": u} for (h, u) in sorted(self.items) if host in (None, h)]

    def get_password(self, item):
        h, u = item.split("|")
        return self.items.get((h, u))

    def delete(self, item):
        h, u = item.split("|")
        return self.items.pop((h, u), None) is not None


@unittest.skipUnless(HAVE_DISPLAY and os.environ.get("DISPLAY"), "needs a display")
class WindowFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        os.makedirs(bb.CONFIG_DIR, exist_ok=True)
        with open(bb.CONFIG_FILE, "w") as f:
            json.dump({"homepage": cls.base + "/blank", "open_homepage_on_startup": True,
                       "first_run_greeted": True, "tracker_lists_enabled": True}, f)
        # a tracker "list" that blocks third-party loads from the name `localhost`
        os.makedirs(bb.CACHE_DIR, exist_ok=True)
        with open(bb.TRACKER_LIST_CACHE, "w") as f:
            json.dump({"fetched": time.time(), "domains": ["localhost"]}, f)
        bb.BharatBrowserWindow._maybe_refresh_tracker_list = lambda self: False
        bb.BharatBrowserWindow.start_auto_git_update_check = lambda self: False
        cls.win = bb.BharatBrowserWindow()
        cls.win.show_all()
        cls.win.secrets = FakeSecrets()
        assert spin(lambda: cls.win.content_filter is not None, 20), "content filter never compiled"

    @classmethod
    def tearDownClass(cls):
        cls.win.destroy()
        cls.server.shutdown()
        shutil.rmtree(_HOME, ignore_errors=True)

    def setUp(self):
        self.win.site_settings.clear()
        self.win._clear_infobar()
        Handler.hits.clear()

    def load(self, path, wait_title=None):
        wv = self.win.get_active_webview()
        wv.load_uri(self.base + path)
        self.assertTrue(spin(lambda: not wv.is_loading() and (wv.get_uri() or "").endswith(path.split("?")[0])
                             and (wait_title is None or wv.get_title() == wait_title), 10), f"{path} did not load")
        return wv

    # ---- tabs ----------------------------------------------------------
    def test_pin_reorder_close_reopen_and_session(self):
        win = self.win
        win.create_new_tab(self.base + "/blank")
        win.create_new_tab(self.base + "/done")
        spin(lambda: all(not win.notebook.get_nth_page(i)._bharat_webview.is_loading() for i in range(win.notebook.get_n_pages())))
        last = win.notebook.get_nth_page(win.notebook.get_n_pages() - 1)
        win.set_tab_pinned(last, True)
        self.assertEqual(win.notebook.page_num(last), 0, "pinned tab moves to the front")
        self.assertFalse(last._bharat_close_btn.get_visible())
        self.assertTrue(last._bharat_label.get_text().startswith("📌"))
        urls, pinned = win._collect_session()
        self.assertEqual(pinned, [0])
        win._run_session_save()
        with open(bb.SESSION_FILE) as f:
            self.assertEqual(json.load(f)["pinned"], [0])

        n = win.notebook.get_n_pages()
        win.close_other_tabs(win.notebook.get_nth_page(1))
        self.assertEqual(win.notebook.get_n_pages(), 2, "close-others keeps the chosen tab and pinned tabs")
        self.assertTrue(win._closed_tabs)
        before = win.notebook.get_n_pages()
        win.reopen_closed_tab()
        self.assertEqual(win.notebook.get_n_pages(), before + 1)
        win.set_tab_pinned(last, False)
        self.assertTrue(last._bharat_close_btn.get_visible())

        ev = Gdk.EventButton()  # what the "button-press-event" signal really passes to handlers
        ev.type = Gdk.EventType.BUTTON_PRESS
        ev.button = 2
        count = win.notebook.get_n_pages()
        win._on_tab_header_click(win.get_active_tab_box(), ev)
        self.assertEqual(win.notebook.get_n_pages(), count - 1, "middle-click closes the tab")

    # ---- per-site policy ------------------------------------------------
    def test_per_site_javascript(self):
        wv = self.load("/js", wait_title="js-ran")
        self.assertEqual(wv.get_title(), "js-ran")
        bb.set_site_value(self.win.site_settings, "127.0.0.1", "javascript", False)
        wv = self.load("/js", wait_title="js-off")
        self.assertEqual(wv.get_title(), "js-off", "JavaScript is off for this site")
        self.win.site_settings.clear()
        wv = self.load("/js", wait_title="js-ran")
        self.assertEqual(wv.get_title(), "js-ran", "and back on again")

    def test_per_site_ad_blocking(self):
        self.load("/third")
        spin(lambda: False, 1.0)
        self.assertEqual(Handler.hits.get("/pixel", 0), 0, "tracker-list rule blocks the third-party load")
        bb.set_site_value(self.win.site_settings, "127.0.0.1", "adblock", False)
        self.load("/third")
        self.assertTrue(spin(lambda: Handler.hits.get("/pixel", 0) > 0, 5), "ad blocking off for this site lets it load")

    def test_zoom_is_remembered_per_site(self):
        wv = self.load("/blank")
        self.win.adjust_zoom(0.3)
        self.assertAlmostEqual(self.win.site_settings["127.0.0.1"]["zoom"], 1.3, places=2)
        wv.set_zoom_level(1.0)
        wv = self.load("/done")
        self.assertTrue(spin(lambda: abs(wv.get_zoom_level() - 1.3) < 0.01, 5), "saved zoom re-applied on load")
        self.win.adjust_zoom(reset=True)
        self.assertNotIn("zoom", self.win.site_settings.get("127.0.0.1", {}))

    # ---- permissions ----------------------------------------------------
    def test_remembered_permissions_skip_the_prompt(self):
        wv = self.load("/blank")
        calls = []

        class Req:
            def allow(self): calls.append("allow")
            def deny(self): calls.append("deny")

        original = self.win._permission_kind
        self.win._permission_kind = lambda r: "media"
        try:
            bb.set_site_value(self.win.site_settings, "127.0.0.1", "permissions", "allow", sub="media")
            self.assertTrue(self.win.on_permission_request(wv, Req()))
            bb.set_site_value(self.win.site_settings, "127.0.0.1", "permissions", "deny", sub="media")
            self.win.on_permission_request(wv, Req())
        finally:
            self.win._permission_kind = original
        self.assertEqual(calls, ["allow", "deny"])

    # ---- https warning --------------------------------------------------
    def test_https_warning_and_one_time_token(self):
        wv = self.load("/blank")
        self.win._show_https_warning(wv, "https://plain-only.example/page", "connection refused")
        self.assertTrue(spin(lambda: "Secure connection unavailable" in (js(wv, "document.body.innerText") or ""), 8))
        href = js(wv, "document.querySelector('a.alt').href")
        self.assertTrue(href.startswith("bharat://allow-http?t="))

        class SchemeReq:
            def __init__(self, uri): self.uri, self.body = uri, None
            def get_uri(self): return self.uri
            def finish(self, stream, length, ctype): self.body = stream.read_bytes(length, None).get_data().decode()

        forged = SchemeReq("bharat://allow-http?t=guess")
        self.win._on_bharat_scheme(forged, None)
        self.assertNotIn("plain-only.example", self.win._http_allowed_hosts, "a guessed token does nothing")
        real = SchemeReq(href)
        self.win._on_bharat_scheme(real, None)
        self.assertIn("plain-only.example", self.win._http_allowed_hosts)
        self.assertIn("http://plain-only.example/page", real.body)
        again = SchemeReq(href)
        self.win._on_bharat_scheme(again, None)
        self.assertNotIn("refresh", again.body, "the token is single-use")
        self.win._http_allowed_hosts.discard("plain-only.example")

    # ---- reader mode ----------------------------------------------------
    def test_reader_mode_opens_on_articles_only(self):
        wv = self.load("/article")
        self.win.toggle_reader_mode()
        self.assertTrue(spin(lambda: js(wv, "String(!!document.getElementById('__bharat_reader'))") == "true", 8))
        self.win.toggle_reader_mode()
        self.assertTrue(spin(lambda: js(wv, "String(!!document.getElementById('__bharat_reader'))") == "false", 8), "second call closes it")
        wv = self.load("/thin")
        self.win.toggle_reader_mode()
        spin(lambda: False, 0.5)
        self.assertEqual(js(wv, "String(!!document.getElementById('__bharat_reader'))"), "false", "no article, no reader")

    # ---- passwords ------------------------------------------------------
    def test_password_save_then_fill(self):
        wv = self.load("/login")
        js(wv, "document.getElementById('u').value='alice';document.getElementById('p').value='hunter2';"
               "document.getElementById('go').click();'x'")
        self.assertTrue(spin(lambda: self.win._infobar is not None, 6), "save prompt appears after submit")
        self.win._infobar.response(1)  # "Save"
        self.assertEqual(self.win.secrets.items, {("127.0.0.1", "alice"): "hunter2"})

        wv = self.load("/login")
        self.assertTrue(spin(lambda: self.win._infobar is not None, 6), "fill offer appears on a login page")
        self.win._infobar.response(1)  # "Fill"
        self.assertTrue(spin(lambda: js(wv, "document.getElementById('p').value") == "hunter2", 5))
        self.assertEqual(js(wv, "document.getElementById('u').value"), "alice")
        self.win.secrets.items.clear()

    def test_password_prompt_respects_never_and_private(self):
        self.win.site_settings["127.0.0.1"] = {"passwords": False}
        wv = self.load("/login")
        js(wv, "document.getElementById('u').value='bob';document.getElementById('p').value='pw';document.getElementById('go').click();'x'")
        spin(lambda: False, 1.5)
        self.assertIsNone(self.win._infobar, "'never for this site' suppresses the prompt")

    # ---- stats / report / import ---------------------------------------
    def test_privacy_report_and_stats(self):
        wv = self.win.get_active_webview()
        before = self.win.stats["blocked"]
        self.win._count_blocked(wv, "https://tracker.evil.example/x.js")
        self.assertEqual(self.win.stats["blocked"], before + 1)
        html = self.win.build_privacy_report_html()
        self.assertIn("tracker.evil.example", html)
        self.assertIn("Privacy Report", html)
        self.win._run_session_save()
        self.assertGreaterEqual(bb.load_privacy_stats()["blocked"], before + 1, "stats are persisted")

    def test_import_merges_into_live_lists(self):
        marks = [{"url": "https://imp.example/", "title": "Imp", "added": 1}]
        hist = [{"url": "https://imp.example/h", "title": "H", "visits": 2, "total_seconds": 0.0, "last_visited": 5}]
        added_b, added_h = self.win._apply_import(marks, hist)
        self.assertEqual((added_b, added_h), (1, 1))
        self.assertEqual(self.win._apply_import(marks, hist), (0, 0), "importing twice adds nothing")
        with open(bb.BOOKMARKS_FILE) as f:
            self.assertIn("https://imp.example/", [b["url"] for b in json.load(f)])
        self.assertIn("https://imp.example/h", [row[0] for row in self.win.url_completion_store])

    def test_private_window_never_writes_site_settings(self):
        private = bb.BharatBrowserWindow(private=True)
        try:
            self.assertEqual(private.site_settings, {})
            bb.set_site_value(private.site_settings, "x.test", "zoom", 2.0)
            before = os.path.exists(bb.SITE_SETTINGS_FILE) and open(bb.SITE_SETTINGS_FILE).read()
            private._save_site_settings()
            after = os.path.exists(bb.SITE_SETTINGS_FILE) and open(bb.SITE_SETTINGS_FILE).read()
            self.assertEqual(before, after)
            self.assertIsNone(private.password_script, "no password detection in private windows")
        finally:
            private.destroy()


if __name__ == "__main__":
    unittest.main()
