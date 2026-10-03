#!/usr/bin/env python3
"""Tests for the pure analysis logic in bharat-diagnose.py (synthetic runs; no browser needed)."""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location("bd", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bharat-diagnose.py"))
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)


def proc(role, pid, pss_mb, cpu=1.0, fds=50, swap=0):
    return {"pid": pid, "role": role, "rss_kb": int(pss_mb * 1024 * 1.5), "pss_kb": int(pss_mb * 1024), "swap_kb": swap,
            "threads": 10, "fds": fds, "cpu": cpu, "cpu_ticks": 0, "start_ticks": 0, "read_kb": 0, "write_kb": 0}


def make_run(seconds, step, main_mb, web_mb, main_fds=lambda t: 50, avail_mb=4000, psi=0.0, web_cpu=1.0, main_cpu=1.0):
    r = bd.Run()
    t = 0.0
    while t <= seconds:
        r.samples.append({"t": t,
                          "procs": [proc("main", 1, main_mb(t), cpu=main_cpu, fds=main_fds(t)), proc("web_process", 2, web_mb(t), cpu=web_cpu)],
                          "sys": {"mem_total_kb": 8000 * 1024, "mem_avail_kb": int(avail_mb * 1024), "swap_total_kb": 0,
                                  "swap_free_kb": 0, "psi_mem_some10": psi}})
        t += step
    return r


def titles(findings, sev=None):
    return [f["title"] for f in findings if sev is None or f["severity"] == sev]


class Analysis(unittest.TestCase):
    def test_linear_fit(self):
        s, r2 = bd.linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
        self.assertAlmostEqual(s, 2.0)
        self.assertAlmostEqual(r2, 1.0)
        self.assertEqual(bd.linear_fit([1, 1, 1], [1, 2, 3]), (0.0, 0.0))
        self.assertEqual(bd.linear_fit([1], [1]), (0.0, 0.0))

    def test_clean_run_has_no_actionable_findings(self):
        r = make_run(600, 5, lambda t: 100, lambda t: 300)
        self.assertEqual(titles(bd.analyze_run(r), "HIGH") + titles(bd.analyze_run(r), "MEDIUM"), [])

    def test_leak_in_main_process_is_flagged(self):
        r = make_run(600, 5, lambda t: 100 + t / 60 * 8, lambda t: 300)       # +8 MB/min
        f = bd.analyze_run(r)
        self.assertIn("Memory keeps growing during the run", titles(f, "HIGH"))
        self.assertIn("Main (Python/GTK) process memory is growing", titles(f, "HIGH"))

    def test_renderer_growth_is_total_not_main(self):
        r = make_run(600, 5, lambda t: 100, lambda t: 300 + t / 60 * 8)
        f = bd.analyze_run(r)
        self.assertIn("Memory keeps growing during the run", titles(f))
        self.assertNotIn("Main (Python/GTK) process memory is growing", titles(f))

    def test_second_instance_starting_midrun_is_not_a_leak(self):
        # Regression: a flat main process plus two more browser instances appearing at t=525 s used to
        # be summed into one series and flagged as "main process memory is growing".
        r = make_run(780, 5, lambda t: 220, lambda t: 300)
        for s in r.samples:
            if s["t"] >= 525:
                s["procs"] += [proc("main", 3, 205), proc("main", 4, 205)]
        self.assertNotIn("Main (Python/GTK) process memory is growing", titles(bd.analyze_run(r)))

    def test_noise_without_trend_is_not_a_leak(self):
        r = make_run(600, 5, lambda t: 100 + (7 if int(t / 5) % 2 else -7), lambda t: 300)
        self.assertNotIn("Memory keeps growing during the run", titles(bd.analyze_run(r)))

    def test_short_run_refuses_to_judge_growth(self):
        r = make_run(60, 5, lambda t: 100 + t * 5, lambda t: 300)
        f = bd.analyze_run(r)
        self.assertIn("Run too short for leak detection", titles(f))
        self.assertNotIn("Memory keeps growing during the run", titles(f))

    def test_fd_leak(self):
        r = make_run(600, 5, lambda t: 100, lambda t: 300, main_fds=lambda t: 50 + int(t / 60 * 30))
        self.assertIn("Main process open file descriptors keep growing", titles(bd.analyze_run(r)))

    def test_renderer_exits_and_browser_exit(self):
        r = make_run(300, 5, lambda t: 100, lambda t: 300)
        r.events = [{"t": i * 10, "kind": "exit", "pid": 10 + i, "role": "web_process"} for i in range(4)]
        self.assertIn("WebKit renderer processes exited repeatedly", titles(bd.analyze_run(r)))
        r.main_exited = True
        # No journal evidence: a normal quit is INFO, never an alarm
        self.assertIn("Browser session ended", titles(bd.analyze_run(r), "INFO"))
        self.assertNotIn("Browser exited unexpectedly", titles(bd.analyze_run(r)))
        # With crash evidence it escalates
        self.assertIn("Browser exited unexpectedly", titles(bd.analyze_run(r, ["segfault in WebKitWebProcess"]), "HIGH"))

    def test_memory_pressure(self):
        r = make_run(300, 5, lambda t: 100, lambda t: 300, avail_mb=300, psi=25.0)
        self.assertIn("System ran short of memory while the browser was running", titles(bd.analyze_run(r), "HIGH"))

    def test_busy_main_process(self):
        r = make_run(300, 5, lambda t: 100, lambda t: 300, main_cpu=40.0)
        self.assertIn("Main process uses noticeable CPU", titles(bd.analyze_run(r)))

    def test_no_samples(self):
        self.assertEqual(titles(bd.analyze_run(bd.Run())), ["No data collected"])


class Watch(unittest.TestCase):
    def test_sample_store_is_bounded_and_keeps_both_ends(self):
        st = bd.SampleStore(cap=100)
        for i in range(10_000):
            st.add({"t": float(i)})
        self.assertLessEqual(len(st.items), 100)
        self.assertEqual(st.items[0]["t"], 0.0)
        self.assertGreater(st.items[-1]["t"], 9_000)           # recent end is still represented
        ts = [x["t"] for x in st.items]
        self.assertEqual(ts, sorted(ts))

    def test_prune_only_removes_old_session_logs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            names = [f"session-20260101-00000{i}.json" for i in range(5)]
            for n in names + ["notes.json", "session-keep.txt"]:
                open(os.path.join(d, n), "w").close()
            removed = bd.prune_sessions(d, 2)
            self.assertEqual(removed, names[:3])
            self.assertEqual(sorted(os.listdir(d)), sorted(names[3:] + ["notes.json", "session-keep.txt"]))

    def test_latest_link_points_at_newest_session(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for n in ("session-20260101-000001.json", "session-20260101-000002.json"):
                open(os.path.join(d, n), "w").close()
                bd.update_latest_link(d, os.path.join(d, n))
            self.assertEqual(os.readlink(os.path.join(d, "latest.json")), "session-20260101-000002.json")


class SessionLifecycle(unittest.TestCase):
    def test_full_session_appear_sample_exit_report(self):
        import tempfile

        class FakeMonitor:           # "browser" = this test process, which vanishes after 4 polls
            def __init__(self): self.calls = 0
            def find_browser_pids(self):
                self.calls += 1
                return {os.getpid(): "main"} if self.calls <= 4 else {}

        bd.STOP["flag"] = False
        run = bd.collect(FakeMonitor(), interval=0.01, duration=None, wait_for_browser=0, quiet=True)
        self.assertTrue(run.main_exited)
        self.assertEqual(len(run.samples), 4)
        self.assertEqual(run.samples[0]["procs"][0]["role"], "main")
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, "logs", "session-20260101-000000.json")
            findings = bd.finish_session(run, notify=False, json_path=jp, started_at=1_700_000_000.0)
            self.assertIn("Browser session ended", [f["title"] for f in findings])
            import json
            log = json.load(open(jp))
            self.assertEqual(log["schema"], 1)
            self.assertTrue(log["browser_exited"])
            self.assertEqual(len(log["samples"]), 4)
            self.assertIn("main", log["summary"]["roles"])
            self.assertEqual(sorted(log["summary"]["issues"]), ["HIGH", "LOW", "MEDIUM"])
            self.assertFalse(os.path.exists(jp + ".tmp"))
            for key in ("homepage", "search_engine", "download_dir"):   # settings that can hold URLs/paths
                self.assertNotIn(key, log["settings"])

    def test_stop_flag_ends_waiting(self):
        class Never:
            def find_browser_pids(self): return {}
        bd.STOP["flag"] = True
        try:
            self.assertFalse(bd.wait_for_browser(Never(), poll=0.01))
        finally:
            bd.STOP["flag"] = False


class Static(unittest.TestCase):
    ENV = {"render_node": True, "gpu_setting": True, "session": "x11"}

    def test_tab_suspension_off_with_many_renderers(self):
        f = bd.analyze_static({"tab_suspension_enabled": False}, [], self.ENV, 6)
        self.assertIn("Tab suspension is off while many renderers are running", titles(f))
        self.assertNotIn("Tab suspension is off while many renderers are running",
                         titles(bd.analyze_static({"tab_suspension_enabled": False}, [], self.ENV, 2)))
        self.assertNotIn("Tab suspension is off while many renderers are running",
                         titles(bd.analyze_static({"tab_suspension_enabled": True}, [], self.ENV, 6)))

    def test_gpu_on_without_render_node(self):
        env = dict(self.ENV, render_node=False)
        self.assertIn("GPU acceleration is on but no GPU render node was found",
                      titles(bd.analyze_static({"gpu_acceleration_enabled": True}, [], env, 0)))

    def test_stale_and_large_data(self):
        now = 10_000_000.0
        entries = [{"name": "GPUCache", "mb": 50.0, "newest": now - 40 * 86400},
                   {"name": "storage", "mb": 400.0, "newest": now - 3600}]
        f = bd.analyze_static({}, entries, self.ENV, 0, now=now)
        self.assertIn("Browser data directory is large", titles(f))
        stale = [x for x in f if x["title"].startswith("Stale data")][0]
        self.assertIn("GPUCache", stale["evidence"])
        self.assertNotIn("storage", stale["evidence"])          # recently used data is never listed as stale


class Privacy(unittest.TestCase):
    def test_journal_urls_are_redacted(self):
        self.assertEqual(bd.URL_RE.sub("<url>", "crash at https://bank.example/acct?id=1 now"), "crash at <url> now")

    def test_config_scan_reads_sizes_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "history.json"), "w") as f:
                f.write("https://secret.example")
            e = bd.scan_config_dir(d)
            self.assertEqual([x["name"] for x in e], ["history.json"])
            self.assertEqual(set(e[0]), {"name", "mb", "newest"})   # no content field


if __name__ == "__main__":
    unittest.main(verbosity=1)
