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
        self.assertIn("Browser exited during monitoring", titles(bd.analyze_run(r), "HIGH"))

    def test_memory_pressure(self):
        r = make_run(300, 5, lambda t: 100, lambda t: 300, avail_mb=300, psi=25.0)
        self.assertIn("System ran short of memory while the browser was running", titles(bd.analyze_run(r), "HIGH"))

    def test_busy_main_process(self):
        r = make_run(300, 5, lambda t: 100, lambda t: 300, main_cpu=40.0)
        self.assertIn("Main process uses noticeable CPU", titles(bd.analyze_run(r)))

    def test_no_samples(self):
        self.assertEqual(titles(bd.analyze_run(bd.Run())), ["No data collected"])


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
