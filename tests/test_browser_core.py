#!/usr/bin/env python3
"""Tests for the browser's pure logic (no window needed). Run: python3 -m unittest discover -s tests -v"""
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("bb", os.path.join(ROOT, "bharat_browser.py"))
bb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bb)


def write_text(path, text):
    with open(path, "w") as f:
        f.write(text)


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class UrlHelperTests(unittest.TestCase):
    def test_sanitize_url_strips_tracking_params(self):
        self.assertEqual(bb.sanitize_url("https://x.com/a?utm_source=t&id=5&fbclid=z"), "https://x.com/a?id=5")
        self.assertEqual(bb.sanitize_url("https://x.com/a?id=5"), "https://x.com/a?id=5")
        self.assertEqual(bb.sanitize_url("https://x.com/a"), "https://x.com/a")

    def test_homepage_rejects_dangerous_schemes(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", ""):
            self.assertEqual(bb.sanitize_homepage_url(bad), bb.DEFAULT_HOMEPAGE)
        self.assertEqual(bb.sanitize_homepage_url("example.com"), "https://example.com")

    def test_local_network_hosts(self):
        for h in ("localhost", "192.168.1.1", "10.0.0.5", "printer"):
            self.assertTrue(bb.is_local_network_host(h), h)
        self.assertFalse(bb.is_local_network_host("example.com"))

    def test_builtin_rules_cover_domains_paths_and_streaming(self):
        rules = json.loads(bb.build_content_blocker_rules_json())
        filters = [r["trigger"]["url-filter"] for r in rules if r["action"]["type"] == "block"]
        self.assertTrue(any("doubleclick" in f for f in filters))
        paths = [r for r in rules if r["action"]["type"] == "block" and "resource-type" in r["trigger"]]
        self.assertEqual(len(paths), len(bb.BLOCKED_PATH_SEGMENTS))
        for r in paths:
            self.assertNotIn("document", r["trigger"]["resource-type"], "page navigations are never blocked by path")
            self.assertIn("*youtube.com", r["trigger"]["unless-domain"])
        kinds = [r["action"]["type"] for r in rules]
        # manifest exemptions must come after the path rules and before the domain rules
        self.assertLess(max(i for i, r in enumerate(rules) if "resource-type" in r["trigger"]), kinds.index("ignore-previous-rules"))
        for r in rules:
            self.assertNotIn("|", r["trigger"]["url-filter"], "WebKit content rules do not support alternation")

    def test_probe_picks_only_a_clear_winner(self):
        before = {10: 100, 11: 100, 12: 100}
        self.assertEqual(bb.pick_probe_pid(before, {10: 101, 11: 125, 12: 100}), 11)
        self.assertIsNone(bb.pick_probe_pid(before, {10: 101, 11: 102, 12: 100}), "too little CPU moved to trust")
        self.assertIsNone(bb.pick_probe_pid(before, {10: 122, 11: 125, 12: 100}), "two processes moved about equally")
        self.assertIsNone(bb.pick_probe_pid({}, {}), "no renderers at all")
        self.assertIsNone(bb.pick_probe_pid({10: None}, {10: 50}), "unreadable process is skipped")

    def test_memory_text(self):
        self.assertEqual(bb.format_memory_mb(None), "—")
        self.assertEqual(bb.format_memory_mb(51.4), "51 MB")
        self.assertEqual(bb.format_memory_mb(1536), "1.5 GB")

    def test_proc_readers_on_this_process(self):
        self.assertGreater(bb._proc_pss_mb(os.getpid()), 1.0)
        self.assertIsInstance(bb._proc_ticks(os.getpid()), int)
        self.assertIsNone(bb._proc_ticks(2 ** 22 + 12345))
        self.assertIsNone(bb._proc_pss_mb(2 ** 22 + 12345))
        self.assertIsInstance(bb.web_process_pids(), list)

    def test_site_host_of(self):
        self.assertEqual(bb.site_host_of("https://WWW.Example.com:8080/x"), "www.example.com")
        self.assertEqual(bb.site_host_of(""), "")
        self.assertEqual(bb.site_host_of("about:blank"), "")


class OfflineAndPagesTests(unittest.TestCase):
    def test_offline_detection_is_conservative(self):
        self.assertTrue(bb.looks_offline("Error resolving 'x.com': Temporary failure in name resolution"))
        self.assertTrue(bb.looks_offline("Network is unreachable"))
        # per-site problems must not be blamed on the user's internet
        if bb.Gio.NetworkMonitor.get_default().get_network_available():
            self.assertFalse(bb.looks_offline("Connection refused"))
            self.assertFalse(bb.looks_offline("Error resolving 'typo.invalid': Name or service not known"))

    def test_error_page_escapes_hostile_input(self):
        evil = 'https://x.com/"><script>alert(1)</script>'
        for offline in (True, False):
            html = bb.build_error_page('ev"il<h>', evil, "err <b>&@HEADING@", offline)
            self.assertNotIn("<script>alert", html)
            self.assertNotIn('ev"il<h>', html)
            self.assertIn("&lt;b&gt;&amp;@HEADING@", html, "error text must not be re-substituted")

    def test_notice_page(self):
        html = bb.build_notice_page("⚠️", "Heading", "<p>body</p>", '<a class="btn" href="#">Go</a>', tech="a<b")
        self.assertIn("Heading", html)
        self.assertIn("a&lt;b", html)
        self.assertNotIn("@ACTIONS@", html)


class ImportTests(TmpDirCase):
    def test_chromium_bookmarks_and_history(self):
        prof = os.path.join(self.tmp, "Default")
        os.makedirs(prof)
        write_json(os.path.join(prof, "Bookmarks"), {"roots": {"bookmark_bar": {"type": "folder", "children": [
            {"type": "url", "name": "Example", "url": "https://example.com/", "date_added": "13350000000000000"},
            {"type": "folder", "children": [{"type": "url", "name": "Deep", "url": "http://deep.test/"}]},
            {"type": "url", "name": "JS", "url": "javascript:alert(1)"}]}}})
        conn = sqlite3.connect(os.path.join(prof, "History"))
        conn.execute("CREATE TABLE urls (url TEXT, title TEXT, visit_count INT, last_visit_time INT)")
        conn.execute("INSERT INTO urls VALUES ('https://a.test/', 'A', 3, 13350000000000000)")
        conn.execute("INSERT INTO urls VALUES ('chrome://settings', 'S', 1, 13350000000000001)")
        conn.commit(); conn.close()
        marks = bb.read_chromium_bookmarks(prof)
        self.assertEqual({m["url"] for m in marks}, {"https://example.com/", "http://deep.test/"})
        self.assertTrue(1.6e9 < marks[0]["added"] < 1.8e9, "1601-epoch conversion")
        hist = bb.read_chromium_history(prof)
        self.assertEqual([h["url"] for h in hist], ["https://a.test/"])
        self.assertEqual(hist[0]["visits"], 3)

    def test_firefox_bookmarks_and_history(self):
        prof = os.path.join(self.tmp, "x.default")
        os.makedirs(prof)
        conn = sqlite3.connect(os.path.join(prof, "places.sqlite"))
        conn.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INT, last_visit_date INT)")
        conn.execute("CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INT, fk INT, title TEXT, dateAdded INT)")
        conn.execute("INSERT INTO moz_places VALUES (1,'https://ff.test/','FF',2,1700000000000000)")
        conn.execute("INSERT INTO moz_places VALUES (2,'place:sort=1','Q',0,NULL)")
        conn.execute("INSERT INTO moz_bookmarks VALUES (1,1,1,'Bm',1700000000000000)")
        conn.execute("INSERT INTO moz_bookmarks VALUES (2,2,NULL,'folder',1)")
        conn.commit(); conn.close()
        self.assertEqual([b["url"] for b in bb.read_firefox_bookmarks(prof)], ["https://ff.test/"])
        self.assertEqual(bb.read_firefox_bookmarks(prof)[0]["added"], 1700000000)
        self.assertEqual([h["url"] for h in bb.read_firefox_history(prof)], ["https://ff.test/"])

    def test_find_profiles_and_read_profile_survives_bad_files(self):
        base = os.path.join(self.tmp, ".config", "google-chrome", "Default")
        os.makedirs(base)
        write_text(os.path.join(base, "Bookmarks"), "{not json")
        profiles = bb.find_importable_profiles(home=self.tmp)
        self.assertEqual([(p["browser"], p["profile"], p["kind"]) for p in profiles], [("Google Chrome", "Default", "chromium")])
        marks, hist, errors = bb.read_profile(profiles[0])
        self.assertEqual((marks, hist), ([], []))
        self.assertEqual(len(errors), 2, "both parts report their failure instead of crashing")

    def test_netscape_bookmarks_html(self):
        html = """<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>
        <DT><H3>Folder</H3><DL><p>
        <DT><A HREF="https://a.test/?x=1&amp;y=2" ADD_DATE="1700000000" ICON="data:image/png;base64,AAAA">A &amp; B</A>
        <DT><a add_date="1600000000" href="http://b.test/">  <b>Bee</b> </a>
        <DT><A HREF="javascript:alert(1)">bad</A><DT><A HREF="">empty</A><DT><A>nohref</A></DL><p></DL><p>"""
        marks = bb.parse_netscape_bookmarks(html)
        self.assertEqual([(m["url"], m["title"], m["added"]) for m in marks],
                         [("https://a.test/?x=1&y=2", "A & B", 1700000000), ("http://b.test/", "Bee", 1600000000)])

    def test_merge_dedupes_and_respects_limits(self):
        existing = [{"url": "https://a/", "title": "A", "added": 1}]
        merged, added = bb.merge_bookmarks(existing, [{"url": "https://a/", "title": "dup", "added": 2},
                                                     {"url": "https://b/", "title": "B", "added": 3}])
        self.assertEqual((len(merged), added, merged[0]["title"]), (2, 1, "A"))
        _, added = bb.merge_bookmarks(existing, [{"url": f"https://n{i}/", "title": "", "added": 0} for i in range(10)], limit=3)
        self.assertEqual(added, 2)
        hist_old = [{"url": "https://a/", "last_visited": 100}]
        hist_new = [{"url": "https://a/", "last_visited": 999}, {"url": "https://b/", "last_visited": 500}]
        merged, added = bb.merge_history(hist_old, hist_new)
        self.assertEqual([e["url"] for e in merged], ["https://b/", "https://a/"])
        self.assertEqual(merged[1]["last_visited"], 100, "existing entry wins")
        self.assertEqual(added, 1)


class SiteSettingsTests(TmpDirCase):
    def test_set_get_and_cleanup(self):
        s = {}
        bb.set_site_value(s, "a.com", "permissions", "allow", sub="media")
        bb.set_site_value(s, "a.com", "zoom", 1.5)
        self.assertEqual(bb.get_site_permission(s, "a.com", "media"), "allow")
        self.assertIsNone(bb.get_site_permission(s, "a.com", "location"))
        self.assertIsNone(bb.get_site_permission(s, "b.com", "media"))
        bb.set_site_value(s, "a.com", "permissions", None, sub="media")
        bb.set_site_value(s, "a.com", "zoom", None)
        self.assertEqual(s, {}, "empty entries are removed")
        bb.set_site_value(s, "", "zoom", 2)
        self.assertEqual(s, {}, "no host, no entry")

    def test_roundtrip_and_corrupt_file(self):
        old = bb.SITE_SETTINGS_FILE
        bb.SITE_SETTINGS_FILE = os.path.join(self.tmp, "site.json")
        self.addCleanup(setattr, bb, "SITE_SETTINGS_FILE", old)
        self.assertEqual(bb.load_site_settings(), {})
        bb.save_site_settings({"a.com": {"zoom": 1.2}})
        self.assertEqual(bb.load_site_settings(), {"a.com": {"zoom": 1.2}})
        self.assertEqual(oct(os.stat(bb.SITE_SETTINGS_FILE).st_mode & 0o777), "0o600")
        write_text(bb.SITE_SETTINGS_FILE, "garbage")
        self.assertEqual(bb.load_site_settings(), {})


class TrackerListTests(unittest.TestCase):
    LIST = "\n".join([
        "[Adblock Plus 2.0]", "! comment", "||tracker.example^", "||ads.example.net^$third-party",
        "||script.example^$script,third-party", "||only-on-site.example^$domain=news.com", "||neg.example^$~third-party",
        "@@||allowed.example^", "||path.example/ads^", "||wild*.example^", "example.com##.banner",
        "||google.com^", "||x.google.com^", "||recaptcha.net^", "||popup.example^$popup", "||UPPER.Example^",
    ])

    def test_parse_keeps_only_safe_whole_domain_rules(self):
        self.assertEqual(bb.parse_abp_domain_rules(self.LIST),
                         {"tracker.example", "ads.example.net", "script.example", "upper.example"})

    def test_rules_json_is_third_party_only_and_valid(self):
        rules = json.loads(bb.build_content_blocker_rules_json({"tracker.example"}))
        extra = [r for r in rules if "tracker\\.example" in r["trigger"]["url-filter"]]
        self.assertEqual(len(extra), 1)
        for r in extra:
            self.assertEqual(r["trigger"]["load-type"], ["third-party"])
            self.assertEqual(r["action"], {"type": "block"})
        base = json.loads(bb.build_content_blocker_rules_json())
        self.assertEqual(len(rules) - len(base), 1)

    def test_cache_roundtrip(self):
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        old = bb.TRACKER_LIST_CACHE
        bb.TRACKER_LIST_CACHE = os.path.join(tmp, "t.json")
        self.addCleanup(setattr, bb, "TRACKER_LIST_CACHE", old)
        self.assertEqual(bb.load_tracker_list_cache(), (set(), 0.0))
        write_json(bb.TRACKER_LIST_CACHE, {"fetched": 5, "domains": ["a.example", 7]})
        self.assertEqual(bb.load_tracker_list_cache(), ({"a.example"}, 5.0))


class StatsTests(TmpDirCase):
    def test_roundtrip_and_defaults(self):
        old = bb.STATS_FILE
        bb.STATS_FILE = os.path.join(self.tmp, "s.json")
        self.addCleanup(setattr, bb, "STATS_FILE", old)
        fresh = bb.load_privacy_stats()
        self.assertEqual((fresh["blocked"], fresh["params"], fresh["https"]), (0, 0, 0))
        bb.save_privacy_stats({"since": 1.0, "blocked": 4, "params": 2, "https": 9})
        self.assertEqual(bb.load_privacy_stats(), {"since": 1.0, "blocked": 4, "params": 2, "https": 9})


class UpdateInfoTests(unittest.TestCase):
    """fetch_release_info must prefer the API, fall back to the cached raw address, and fail loudly."""

    def _run(self, responses):
        calls = []

        class Resp:
            def __init__(self, status, body): self.status, self.body = status, body
            def read(self, n): return self.body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=0):
            calls.append((req.full_url, req.get_header("Accept")))
            outcome = responses[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return Resp(*outcome)

        original = bb.urllib.request.urlopen
        bb.urllib.request.urlopen = fake_urlopen
        try:
            return bb.fetch_release_info("test", sources=bb.UPDATE_INFO_SOURCES), calls
        finally:
            bb.urllib.request.urlopen = original

    def test_api_answer_wins(self):
        data, calls = self._run([(200, b'{"version": "9.9.9", "sha256": "x"}')])
        self.assertEqual(data["version"], "9.9.9")
        self.assertEqual(len(calls), 1)
        self.assertIn("api.github.com", calls[0][0])
        self.assertEqual(calls[0][1], "application/vnd.github.raw+json")

    def test_falls_back_when_api_is_rate_limited_or_odd(self):
        for first in (OSError("HTTP Error 403: rate limit exceeded"), (200, b"not json"), (200, b'{"nope": 1}'), (500, b"")):
            data, calls = self._run([first, (200, b'{"version": "1.2.3"}')])
            self.assertEqual(data["version"], "1.2.3")
            self.assertIn("raw.githubusercontent.com", calls[1][0])

    def test_raises_when_every_source_fails(self):
        with self.assertRaises(OSError):
            self._run([OSError("boom1"), OSError("boom2")])


@unittest.skipUnless(os.environ.get("BHARAT_TEST_KEYRING", "1") == "1", "keyring tests disabled")
class SecretServiceTests(unittest.TestCase):
    def test_roundtrip_against_real_keyring(self):
        client = bb.SecretServiceClient(application="bharat-browser-test")
        if not client.available():
            self.skipTest(f"no Secret Service on this machine: {client.error}")
        host = f"unittest-{int(time.time())}.example"
        try:
            self.assertTrue(client.store(host, "alice", "s3cret é中"))
            self.assertTrue(client.store(host, "alice", "changed"), "same user replaces, not duplicates")
            found = client.find(host)
            self.assertEqual([(f["host"], f["username"]) for f in found], [(host, "alice")])
            self.assertEqual(client.get_password(found[0]["item"]), "changed")
            self.assertEqual(client.find("other-" + host), [])
        finally:
            for f in client.find(host):
                client.delete(f["item"])
        self.assertEqual(client.find(host), [])


if __name__ == "__main__":
    unittest.main()
