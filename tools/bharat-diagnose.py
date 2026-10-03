#!/usr/bin/env python3
"""
Diagnostic monitor for a running Bharat Browser: samples it, analyzes the run,
and writes a report of findings with concrete suggestions for improving it.

Complements tools/monitor-resources.py (raw RSS/CPU CSV). This tool adds:
  * more signals per process: PSS (true shared-aware memory), swap, threads,
    open file descriptors, disk I/O
  * system context: available memory, swap, memory-pressure (PSI)
  * lifecycle tracking: WebKit subprocess spawns/exits (crash / churn signal)
  * trend analysis: memory and fd growth rates (leak suspects)
  * static checks: settings.json vs observed behaviour, config-dir size and
    stale data, GPU/session environment, recent crash lines from the user journal
  * a ranked findings report (Markdown) plus the raw samples (CSV)

Standard library only. Read-only and external: it never modifies or attaches to
the browser. Privacy: it does NOT read history, cookies, bookmarks, session
contents or page data - only file sizes/mtimes in the config dir, /proc
counters, and journal lines that match crash patterns (URLs are redacted).

Usage:
    python3 tools/bharat-diagnose.py                    # sample until Ctrl+C or browser exit
    python3 tools/bharat-diagnose.py --duration 900     # stop after 15 min
    python3 tools/bharat-diagnose.py --snapshot         # ~10s quick check
    python3 tools/bharat-diagnose.py --output-dir out/  # where report.md / samples.csv go
    python3 tools/bharat-diagnose.py --watch --notify   # diagnose EVERY browser session, forever

Watch mode waits (cheaply) for the browser to start, records the session until
it exits, writes one JSON log per session to <project>/logs/, then waits for the next launch. Run it
at login with tools/diagnose-service.sh install.

Leak detection needs a run of at least 5 minutes; shorter runs say so instead
of guessing. Use the browser normally (or reproduce the problem) while it runs.
"""
import argparse
import csv
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.expanduser("~/.config/bharat-browser")
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")

MIN_TREND_SECONDS = 300          # shortest run that can say anything about growth
MEM_GROWTH_MB_PER_MIN = 5.0      # sustained growth that is flagged
STALE_DAYS = 30
CONFIG_BIG_MB = 300
MAX_STORED_SAMPLES = 2000        # long sessions are thinned so memory stays bounded
DEFAULT_WATCH_DIR = os.path.join(os.path.dirname(HERE), "logs")   # <project>/logs
SESSION_RE = re.compile(r"^session-\d{8}-\d{6}\.json$")
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


# --------------------------------------------------------------------------
# Process discovery: reuse monitor-resources.py's hardened classifier
# --------------------------------------------------------------------------
def load_monitor_module():
    spec = importlib.util.spec_from_file_location("monitor_resources", os.path.join(HERE, "monitor-resources.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# /proc readers (all tolerate the process vanishing mid-read)
# --------------------------------------------------------------------------
def _read(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None


def _kb(text, key):
    m = re.search(rf"^{key}:\s+(\d+) kB", text or "", re.MULTILINE)
    return int(m.group(1)) if m else None


def sample_process(pid, role):
    status = _read(f"/proc/{pid}/status")
    stat = _read(f"/proc/{pid}/stat")
    if status is None or stat is None:
        return None
    try:
        after = stat.rsplit(")", 1)[1].split()
        utime, stime, start_ticks = int(after[11]), int(after[12]), int(after[19])
    except (IndexError, ValueError):
        return None
    m = re.search(r"^Threads:\s+(\d+)", status, re.MULTILINE)
    rollup = _read(f"/proc/{pid}/smaps_rollup")
    io = _read(f"/proc/{pid}/io")
    try:
        fds = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        fds = None
    return {
        "pid": pid, "role": role,
        "rss_kb": _kb(status, "VmRSS") or 0,
        "pss_kb": _kb(rollup, "Pss"),
        "swap_kb": _kb(status, "VmSwap") or 0,
        "threads": int(m.group(1)) if m else None,
        "fds": fds,
        "cpu_ticks": utime + stime,
        "start_ticks": start_ticks,
        "read_kb": _kb_io(io, "read_bytes"),
        "write_kb": _kb_io(io, "write_bytes"),
    }


def _kb_io(text, key):
    m = re.search(rf"^{key}:\s+(\d+)", text or "", re.MULTILINE)
    return int(m.group(1)) // 1024 if m else None


def sample_system():
    mem = _read("/proc/meminfo") or ""
    psi = _read("/proc/pressure/memory") or ""
    m = re.search(r"some avg10=([\d.]+)", psi)
    return {
        "mem_total_kb": _kb(mem, "MemTotal"),
        "mem_avail_kb": _kb(mem, "MemAvailable"),
        "swap_total_kb": _kb(mem, "SwapTotal"),
        "swap_free_kb": _kb(mem, "SwapFree"),
        "psi_mem_some10": float(m.group(1)) if m else None,
    }


# --------------------------------------------------------------------------
# Sampling loop
# --------------------------------------------------------------------------
class Run:
    """Everything observed during one monitoring run."""

    def __init__(self):
        self.samples = []      # [{"t", "procs": [...], "sys": {...}}]
        self.events = []       # [{"t", "kind", "pid", "role"}]
        self.main_exited = False


class SampleStore:
    """Keeps at most `cap` samples by halving resolution when full, so a browser left open for
    days costs the same memory as one open for an hour. Events are never thinned."""

    def __init__(self, cap=MAX_STORED_SAMPLES):
        self.cap, self.items, self.stride, self._n = cap, [], 1, 0

    def add(self, item):
        if self._n % self.stride == 0:
            self.items.append(item)
            if len(self.items) > self.cap:
                self.items = self.items[::2]
                self.stride *= 2
        self._n += 1


STOP = {"flag": False}


def install_signal_handlers():
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.update(flag=True))


def collect(monitor, interval, duration, wait_for_browser, quiet=False):
    """Samples the browser until it exits, `duration` elapses, or STOP is set."""
    run, store = Run(), SampleStore()
    start = time.time()
    known = {}          # pid -> role, from the previous tick
    prev_cpu = {}       # pid -> (ticks, wall)
    seen_any = False
    first_tick = True
    waited_from = time.time()

    while not STOP["flag"]:
        tick = time.time()
        pids = monitor.find_browser_pids()
        if not pids:
            if seen_any:
                run.main_exited = True
                run.events.append({"t": tick - start, "kind": "browser_gone", "pid": None, "role": "main"})
                break
            if tick - waited_from > wait_for_browser:
                break
            time.sleep(1.0)
            continue

        if not seen_any:
            start = tick
        seen_any = True
        procs = []
        for pid, role in pids.items():
            sp = sample_process(pid, role)
            if sp is None:
                continue
            cpu = 0.0
            if pid in prev_cpu:
                dt = tick - prev_cpu[pid][1]
                if dt > 0:
                    cpu = (sp["cpu_ticks"] - prev_cpu[pid][0]) / CLOCK_TICKS / dt * 100.0
            prev_cpu[pid] = (sp["cpu_ticks"], tick)
            sp["cpu"] = cpu
            procs.append(sp)

        t = tick - start
        current = {p["pid"]: p["role"] for p in procs}
        if not first_tick:   # lifecycle events are tracked every tick, even ones thinned out of storage
            for pid, role in current.items():
                if pid not in known:
                    run.events.append({"t": t, "kind": "spawn", "pid": pid, "role": role})
            for pid, role in known.items():
                if pid not in current:
                    run.events.append({"t": t, "kind": "exit", "pid": pid, "role": role})
        first_tick = False
        known = current
        prev_cpu = {pid: v for pid, v in prev_cpu.items() if pid in current}

        sample = {"t": t, "procs": procs, "sys": sample_system()}
        store.add(sample)
        if not quiet:
            _live_line(sample)

        if duration and t >= duration:
            break
        _interruptible_sleep(max(0.0, interval - (time.time() - tick)))
    run.samples = store.items
    return run


def _interruptible_sleep(seconds):
    end = time.time() + seconds
    while not STOP["flag"] and time.time() < end:
        time.sleep(min(0.5, max(0.0, end - time.time())))


def total_mem_kb(sample):
    return sum((p["pss_kb"] if p["pss_kb"] is not None else p["rss_kb"]) for p in sample["procs"])


def _live_line(sample):
    roles = {}
    for p in sample["procs"]:
        roles[p["role"]] = roles.get(p["role"], 0) + 1
    cpu = sum(p["cpu"] for p in sample["procs"])
    avail = sample["sys"].get("mem_avail_kb")
    print(f"[{time.strftime('%H:%M:%S')}] mem={total_mem_kb(sample) / 1024:.0f}MB cpu={cpu:.0f}% "
          f"procs=" + ",".join(f"{r}x{n}" for r, n in sorted(roles.items())) +
          (f" sys_avail={avail / 1024:.0f}MB" if avail else ""))


# --------------------------------------------------------------------------
# Analysis (pure functions: easy to test)
# --------------------------------------------------------------------------
def linear_fit(xs, ys):
    """Least-squares slope and R^2. Returns (slope, r2); (0, 0) if undefined."""
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx, (sxy * sxy) / (sxx * syy)


def _series(samples, fn, warmup):
    pts = [(s["t"], fn(s)) for s in samples if s["t"] >= warmup]
    pts = [(t, v) for t, v in pts if v is not None]
    return [p[0] for p in pts], [p[1] for p in pts]


def finding(severity, title, evidence, suggestion):
    return {"severity": severity, "title": title, "evidence": evidence, "suggestion": suggestion}


def analyze_run(run, exit_crash_lines=None):
    """Findings derived from the sampled behaviour. `exit_crash_lines`: journal evidence of a crash
    around the time the browser exited (None/empty = no evidence, treated as a normal quit)."""
    out = []
    samples = run.samples
    if not samples:
        return [finding("INFO", "No data collected", "Bharat Browser was never observed running.",
                        "Launch the browser, then run this tool again.")]

    duration = samples[-1]["t"]
    warmup = min(60.0, duration * 0.15)

    # --- memory growth (leak suspects), total and per role -------------------
    if duration >= MIN_TREND_SECONDS:
        xs, ys = _series(samples, lambda s: total_mem_kb(s) / 1024.0, warmup)
        slope_s, r2 = linear_fit(xs, ys)
        per_min = slope_s * 60.0
        if per_min > MEM_GROWTH_MB_PER_MIN and r2 > 0.6:
            out.append(finding(
                "HIGH", "Memory keeps growing during the run",
                f"Total memory rose about {per_min:.1f} MB/min over {duration / 60:.0f} min (trend fit R2={r2:.2f}).",
                "Repeat with the same tabs open and no navigation. If it still grows, a leak is likely - see the "
                "per-role rates below to tell whether it is the Python main process or the WebKit renderers."))
        for role in sorted({p["role"] for s in samples for p in s["procs"]}):
            xs, ys = _series(samples, lambda s, r=role: (
                sum((p["pss_kb"] if p["pss_kb"] is not None else p["rss_kb"]) for p in s["procs"] if p["role"] == r) / 1024.0),
                warmup)
            sl, rr = linear_fit(xs, ys)
            if sl * 60 > MEM_GROWTH_MB_PER_MIN and rr > 0.6 and role == "main":
                out.append(finding(
                    "HIGH", "Main (Python/GTK) process memory is growing",
                    f"{sl * 60:.1f} MB/min, R2={rr:.2f}. Renderer growth would be page-driven; main-process growth is "
                    "the browser's own code.",
                    "Look for per-tab/per-navigation state that is never released: history/suggestion caches, "
                    "adblock match caches, download or favicon lists, GLib timers/signal handlers not disconnected on tab close."))
        fx, fy = _series(samples, lambda s: next((p["fds"] for p in s["procs"] if p["role"] == "main"), None), warmup)
        fs, fr = linear_fit(fx, fy)
        if fy and fs * 60 > 5 and fr > 0.6 and fy[-1] - fy[0] > 100:
            out.append(finding(
                "HIGH", "Main process open file descriptors keep growing",
                f"{fy[0]} -> {fy[-1]} fds (+{fs * 60:.1f}/min). This ends in 'too many open files'.",
                "Check for unclosed sockets/files: urllib responses without a context manager, downloads, pipes from subprocess."))
    else:
        out.append(finding(
            "INFO", "Run too short for leak detection",
            f"Observed {duration:.0f}s; trend analysis needs at least {MIN_TREND_SECONDS}s.",
            "Re-run with --duration 600 (or longer) while using the browser normally."))

    # --- lifecycle churn -----------------------------------------------------
    web_exits = [e for e in run.events if e["kind"] == "exit" and e["role"] == "web_process"]
    if run.main_exited and exit_crash_lines:
        out.append(finding("HIGH", "Browser exited unexpectedly",
                           "Crash-like journal lines appeared around the time it exited: " + " | ".join(exit_crash_lines[-3:]),
                           "Match the timestamp with what you were doing; see 'Recent crash lines' for more."))
    elif run.main_exited:
        out.append(finding("INFO", "Browser session ended",
                           "The main process exited. No crash evidence in the journal, so this looks like a normal quit "
                           "(a hard kill leaves no trace, so a crash cannot be fully ruled out).", ""))
    if len(web_exits) >= 3:
        out.append(finding(
            "MEDIUM", "WebKit renderer processes exited repeatedly",
            f"{len(web_exits)} WebProcess exits in {duration / 60:.1f} min. Closing tabs also does this, so it is only "
            "a signal if you were not closing tabs.",
            "If tabs were left open: renderers are crashing or being killed (often the OOM killer under memory pressure). "
            "Check `journalctl -k | grep -i oom` and the memory-pressure finding."))

    # --- CPU ------------------------------------------------------------------
    main_cpu = [p["cpu"] for s in samples[1:] for p in s["procs"] if p["role"] == "main"]
    if len(main_cpu) >= 10:
        avg = sum(main_cpu) / len(main_cpu)
        busy = sum(1 for c in main_cpu if c > 25) / len(main_cpu)
        if avg > 8 or busy > 0.25:
            out.append(finding(
                "MEDIUM", "Main process uses noticeable CPU",
                f"Average {avg:.1f}% CPU; above 25% in {busy * 100:.0f}% of samples. The main process only runs UI, "
                "blocking and filter logic - page work belongs in the renderers.",
                "Profile the main loop: `python3 -m cProfile -o prof.out bharat_browser.py`, then inspect with pstats. "
                "Typical culprits: per-request adblock matching in Python, polling timers, synchronous disk writes."))
    idle_web = [p["cpu"] for s in samples[1:] for p in s["procs"] if p["role"] == "web_process"]
    if len(idle_web) >= 10 and sum(idle_web) / len(idle_web) > 15:
        out.append(finding("LOW", "Renderers average high CPU",
                           f"Average {sum(idle_web) / len(idle_web):.0f}% per WebProcess sample.",
                           "Usually page content (video, animations, ad scripts). Compare with the same page in another "
                           "browser; if only Bharat is high, check GPU acceleration and ad-blocking effectiveness."))

    # --- memory share / per-process size -------------------------------------
    peak = max(total_mem_kb(s) for s in samples) / 1024.0
    webs = [sum(1 for p in s["procs"] if p["role"] == "web_process") for s in samples]
    peak_webs = max(webs) if webs else 0
    if peak_webs:
        avg_per_web = sum(
            (p["pss_kb"] if p["pss_kb"] is not None else p["rss_kb"]) for p in samples[-1]["procs"] if p["role"] == "web_process"
        ) / max(1, webs[-1]) / 1024.0
        if avg_per_web > 400:
            out.append(finding("MEDIUM", "Renderers are large",
                               f"Average {avg_per_web:.0f} MB per WebProcess at the end of the run (peak {peak_webs} renderers).",
                               "Heavy pages dominate. Enable tab suspension so background tabs release their renderer."))

    # --- system pressure -------------------------------------------------------
    last_sys = [s["sys"] for s in samples if s["sys"].get("mem_total_kb")]
    if last_sys:
        psi = max((x["psi_mem_some10"] or 0) for x in last_sys)
        min_avail = min(x["mem_avail_kb"] for x in last_sys if x["mem_avail_kb"] is not None) / 1024.0
        total = last_sys[-1]["mem_total_kb"] / 1024.0
        if psi >= 10 or min_avail < total * 0.08:
            out.append(finding(
                "HIGH", "System ran short of memory while the browser was running",
                f"Memory-pressure (PSI some avg10) peaked at {psi:.1f}; lowest available memory {min_avail:.0f} MB of {total:.0f} MB. "
                f"Browser peak {peak:.0f} MB.",
                "Enable background tab suspension and Low Memory Mode, and close heavy tabs. If this is a small-RAM machine, "
                "also reduce what else is running."))
        sw_total = last_sys[-1].get("swap_total_kb") or 0
        sw_used = sw_total - (last_sys[-1].get("swap_free_kb") or 0)
        browser_swap = max((sum(p["swap_kb"] for p in s["procs"]) for s in samples), default=0) / 1024.0
        if sw_total and sw_used / sw_total > 0.5 and browser_swap > 100:
            out.append(finding("MEDIUM", "Browser memory is being swapped out",
                               f"Browser swap up to {browser_swap:.0f} MB; system swap {sw_used / sw_total * 100:.0f}% used.",
                               "Swapped renderers make tabs feel frozen when switching back. Reduce open tabs or enable tab suspension."))
    out.append(finding("INFO", "Run summary",
                       f"{len(samples)} samples over {duration / 60:.1f} min; peak memory {peak:.0f} MB; peak renderers {peak_webs}.", ""))
    return out


def dir_stats(path):
    """(total_bytes, newest_mtime) for a file or directory tree. Sizes/mtimes only - never contents."""
    total, newest = 0, 0.0
    if os.path.isfile(path) or os.path.islink(path):
        try:
            st = os.lstat(path)
            return st.st_size, st.st_mtime
        except OSError:
            return 0, 0.0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += st.st_size
            newest = max(newest, st.st_mtime)
    return total, newest


def scan_config_dir(path=CONFIG_DIR):
    entries = []
    try:
        names = os.listdir(path)
    except OSError:
        return entries
    for name in names:
        size, newest = dir_stats(os.path.join(path, name))
        entries.append({"name": name, "mb": size / 1048576.0, "newest": newest})
    return sorted(entries, key=lambda e: -e["mb"])


def analyze_static(settings, config_entries, env, peak_web_processes, now=None):
    """Findings from configuration, stored data and environment."""
    now = now or time.time()
    out = []
    settings = settings or {}

    if peak_web_processes >= 4 and not settings.get("tab_suspension_enabled", True):
        out.append(finding(
            "MEDIUM", "Tab suspension is off while many renderers are running",
            f"settings.json: tab_suspension_enabled=false; up to {peak_web_processes} renderers observed.",
            "Turn on Settings > Performance > Background Tab Suspension. It unloads tabs idle for 15+ minutes."))
    if settings.get("low_memory_mode"):
        out.append(finding(
            "INFO", "Low Memory Mode is on",
            "It only shrinks WebKit's cache model; the source notes WEBKIT_USE_SINGLE_WEB_PROCESS is not honoured by "
            "WebKitGTK 2.54, so it does not reduce the renderer count.",
            "Do not expect fewer processes from it; tab suspension is the lever that actually frees renderers."))
    if settings.get("gpu_acceleration_enabled") and not env.get("render_node"):
        out.append(finding(
            "MEDIUM", "GPU acceleration is on but no GPU render node was found",
            "No /dev/dri/renderD* device is present, so WebKit is probably compositing in software.",
            "Check GPU drivers/permissions (user in the `render`/`video` group). In a VM, use a virtual GPU or turn the setting off."))

    total_mb = sum(e["mb"] for e in config_entries)
    if total_mb > CONFIG_BIG_MB:
        top = ", ".join(f"{e['name']} {e['mb']:.0f}MB" for e in config_entries[:4])
        out.append(finding("MEDIUM", "Browser data directory is large",
                           f"{total_mb:.0f} MB in ~/.config/bharat-browser. Largest: {top}.",
                           "Clear cache/site data from the browser, or remove stale entries listed below."))
    stale = [e for e in config_entries if e["mb"] >= 1 and e["newest"] and (now - e["newest"]) > STALE_DAYS * 86400]
    if stale:
        mb = sum(e["mb"] for e in stale)
        names = ", ".join(e["name"] for e in stale[:8])
        chromium_like = any(e["name"] in ("GPUCache", "Code Cache", "Crashpad", "Local State", "Trust Tokens") for e in stale)
        out.append(finding(
            "LOW", "Stale data not touched in 30+ days",
            f"{mb:.0f} MB across: {names}." + (" Names like GPUCache/Code Cache/Crashpad are Chromium-engine artefacts, "
                                                 "so these likely predate the current GTK/WebKit browser." if chromium_like else ""),
            "Review, back up if unsure, then delete to reclaim space. The tool never deletes anything itself."))

    if env.get("psi_unavailable"):
        out.append(finding("INFO", "Memory-pressure data unavailable", "/proc/pressure/memory is not readable on this kernel.", ""))
    return out


# --------------------------------------------------------------------------
# Environment and journal
# --------------------------------------------------------------------------
def read_environment(settings):
    env = {
        "session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "render_node": any(n.startswith("renderD") for n in (os.listdir("/dev/dri") if os.path.isdir("/dev/dri") else [])),
        "gpu_setting": bool((settings or {}).get("gpu_acceleration_enabled")),
        "psi_unavailable": not os.path.exists("/proc/pressure/memory"),
        "cpus": os.cpu_count(),
    }
    mem = _read("/proc/meminfo")
    env["ram_mb"] = (_kb(mem, "MemTotal") or 0) // 1024
    try:
        import gi  # noqa: WPS433 - optional, only for version strings
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2, Gtk
        env["webkit"] = f"{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}"
        env["gtk"] = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    except Exception:
        env["webkit"] = env["gtk"] = "unknown"
    return env


CRASH_RE = re.compile(r"(segfault|SIGSEGV|SIGABRT|Traceback|CRITICAL|core dumped|terminated|crash|killed process)", re.I)
APP_RE = re.compile(r"(bharat|WebKit)", re.I)
URL_RE = re.compile(r"https?://\S+")


def recent_crash_lines(limit=8, since="24 hours ago"):
    """Crash-looking journal lines about the browser (last 24h), with URLs redacted. Best effort."""
    try:
        res = subprocess.run(
            ["journalctl", "--user", "--since", since, "--no-pager", "-o", "cat", "-n", "3000"],
            capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [URL_RE.sub("<url>", l)[:200] for l in res.stdout.splitlines() if APP_RE.search(l) and CRASH_RE.search(l)]
    return lines[-limit:]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def write_csv(run, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_s", "pid", "role", "rss_mb", "pss_mb", "swap_mb", "threads", "fds", "cpu_percent",
                    "read_mb", "write_mb"])
        for s in run.samples:
            for p in s["procs"]:
                w.writerow([f"{s['t']:.1f}", p["pid"], p["role"], f"{p['rss_kb'] / 1024:.1f}",
                            "" if p["pss_kb"] is None else f"{p['pss_kb'] / 1024:.1f}", f"{p['swap_kb'] / 1024:.1f}",
                            p["threads"], p["fds"], f"{p['cpu']:.1f}",
                            "" if p["read_kb"] is None else f"{p['read_kb'] / 1024:.1f}",
                            "" if p["write_kb"] is None else f"{p['write_kb'] / 1024:.1f}"])


def render_report(run, findings, env, config_entries, crash_lines, settings):
    findings = sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])
    L = ["# Bharat Browser diagnostic report", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    actionable = [f for f in findings if f["severity"] != "INFO"]
    L += ["## Findings", ""]
    if not actionable:
        L += ["No problems detected in this run.", ""]
    for f in findings:
        L += [f"### [{f['severity']}] {f['title']}", "", f["evidence"], ""]
        if f["suggestion"]:
            L += [f"**Suggestion:** {f['suggestion']}", ""]

    if run.samples:
        L += ["## Per-role summary", "", "| Role | Peak count | Peak memory (MB) | Avg CPU % |", "|---|---|---|---|"]
        for role in sorted({p["role"] for s in run.samples for p in s["procs"]}):
            counts = [sum(1 for p in s["procs"] if p["role"] == role) for s in run.samples]
            mems = [sum((p["pss_kb"] if p["pss_kb"] is not None else p["rss_kb"]) for p in s["procs"] if p["role"] == role) / 1024
                    for s in run.samples]
            cpus = [sum(p["cpu"] for p in s["procs"] if p["role"] == role) for s in run.samples[1:]] or [0]
            L.append(f"| {role} | {max(counts)} | {max(mems):.0f} | {sum(cpus) / len(cpus):.1f} |")
        L.append("")
    L += ["## Environment", "",
          f"- Session: {env['session']} | CPUs: {env['cpus']} | RAM: {env['ram_mb']} MB",
          f"- WebKit2GTK: {env['webkit']} | GTK: {env['gtk']} | GPU render node: {'yes' if env['render_node'] else 'no'}",
          "- Settings: " + ", ".join(f"{k}={v}" for k, v in sorted((settings or {}).items())
                                     if k not in ("homepage", "download_dir", "search_engine")), ""]
    if config_entries:
        L += ["## Data directory (~/.config/bharat-browser)", "", "| Entry | MB |", "|---|---|"]
        L += [f"| {e['name']} | {e['mb']:.1f} |" for e in config_entries[:10]] + [""]
    L += ["## Recent crash lines (journal, last 24h)", ""]
    if crash_lines is None:
        L += ["Journal not readable."]
    elif not crash_lines:
        L += ["None found."]
    else:
        L += [f"- `{l}`" for l in crash_lines]
    L += ["", "## Method and limits", "",
          "- Memory is PSS (shared pages split fairly) where /proc/<pid>/smaps_rollup is readable, else RSS.",
          "- A WebProcess exit can be a closed tab or a crash; this tool cannot tell which, so it is only flagged in volume.",
          "- Per-site breakdown is deliberately not collected (no page or URL data is read).",
          "- Growth analysis skips the warm-up period and needs a run of 5+ minutes."]
    return "\n".join(L) + "\n"


def load_settings():
    try:
        with open(os.path.join(CONFIG_DIR, "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def role_summary(run):
    out = {}
    for role in sorted({p["role"] for s in run.samples for p in s["procs"]}):
        counts = [sum(1 for p in s["procs"] if p["role"] == role) for s in run.samples]
        mems = [sum((p["pss_kb"] if p["pss_kb"] is not None else p["rss_kb"]) for p in s["procs"] if p["role"] == role) / 1024
                for s in run.samples]
        cpus = [sum(p["cpu"] for p in s["procs"] if p["role"] == role) for s in run.samples[1:]] or [0.0]
        out[role] = {"peak_count": max(counts), "peak_memory_mb": round(max(mems), 1), "avg_cpu_percent": round(sum(cpus) / len(cpus), 1)}
    return out


def session_to_json(run, findings, env, config_entries, crash_lines, settings, started_at):
    """One self-contained, JSON-serializable log of a session. No page, URL or history data."""
    samples = []
    for s in run.samples:
        samples.append({
            "t": round(s["t"], 1),
            "system": s["sys"],
            "processes": [{
                "pid": p["pid"], "role": p["role"], "rss_mb": round(p["rss_kb"] / 1024, 1),
                "pss_mb": None if p["pss_kb"] is None else round(p["pss_kb"] / 1024, 1),
                "swap_mb": round(p["swap_kb"] / 1024, 1), "threads": p["threads"], "fds": p["fds"],
                "cpu_percent": round(p["cpu"], 1), "read_mb": p["read_kb"] and round(p["read_kb"] / 1024, 1),
                "write_mb": p["write_kb"] and round(p["write_kb"] / 1024, 1)} for p in s["procs"]]})
    duration = run.samples[-1]["t"] if run.samples else 0.0
    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])
    return {
        "schema": 1,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)),
        "duration_s": round(duration, 1),
        "browser_exited": run.main_exited,
        "summary": {
            "samples_stored": len(samples),
            "peak_memory_mb": round(max((total_mem_kb(s) for s in run.samples), default=0) / 1024, 1),
            "issues": {sev: sum(1 for f in findings if f["severity"] == sev) for sev in ("HIGH", "MEDIUM", "LOW")},
            "roles": role_summary(run)},
        "findings": ordered,
        "environment": env,
        "settings": {k: v for k, v in (settings or {}).items() if k not in ("homepage", "download_dir", "search_engine")},
        "data_directory_mb": {e["name"]: round(e["mb"], 1) for e in config_entries[:15]},
        "crash_lines_24h": crash_lines or [],
        "events": run.events,
        "samples": samples}


def finish_session(run, out_dir=None, notify=False, json_path=None, started_at=None):
    """Analyze one run. Writes report.md + samples.csv into `out_dir` and/or a JSON log to `json_path`.
    Returns the findings."""
    settings = load_settings()
    env = read_environment(settings)
    config_entries = scan_config_dir()
    peak_webs = max((sum(1 for p in s["procs"] if p["role"] == "web_process") for s in run.samples), default=0)
    exit_lines = recent_crash_lines(since="3 minutes ago") if run.main_exited else None
    findings = analyze_run(run, exit_lines) + analyze_static(settings, config_entries, env, peak_webs)
    crash_lines = recent_crash_lines()
    if crash_lines:
        findings.append(finding("MEDIUM", "Crash-like journal lines in the last 24h",
                                f"{len(crash_lines)} line(s); latest shown in the log.",
                                "Match the timestamps with what you were doing."))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        write_csv(run, os.path.join(out_dir, "samples.csv"))
        with open(os.path.join(out_dir, "report.md"), "w") as f:
            f.write(render_report(run, findings, env, config_entries, crash_lines, settings))
    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        tmp = json_path + ".tmp"
        with open(tmp, "w") as f:       # write-then-rename: a reader never sees a half-written log
            json.dump(session_to_json(run, findings, env, config_entries, crash_lines, settings,
                                      started_at or time.time()), f, indent=1)
        os.replace(tmp, json_path)
    if notify:
        notify_findings(findings, json_path or os.path.join(out_dir, "report.md"))
    return findings


def notify_findings(findings, report_path):
    """Desktop notification when a session has actionable findings. Best effort; silent if unavailable."""
    top = [f for f in sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]]) if f["severity"] in ("HIGH", "MEDIUM")]
    if not top:
        return
    body = "\n".join(f"[{f['severity']}] {f['title']}" for f in top[:3]) + f"\nLog: {report_path}"
    try:
        subprocess.run(["notify-send", "-a", "Bharat diagnostics", f"Bharat Browser: {len(top)} issue(s) found", body],
                       timeout=5, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def prune_sessions(base, keep):
    """Deletes the oldest session-YYYYmmdd-HHMMSS.json logs beyond `keep`. Touches nothing else."""
    try:
        sessions = sorted(f for f in os.listdir(base) if SESSION_RE.match(f) and os.path.isfile(os.path.join(base, f)))
    except OSError:
        return []
    doomed = sessions[:-keep] if keep > 0 else sessions
    for f in doomed:
        try:
            os.unlink(os.path.join(base, f))
        except OSError:
            pass
    return doomed


def update_latest_link(base, session_file):
    link = os.path.join(base, "latest.json")
    try:
        if os.path.islink(link):
            os.unlink(link)
        if not os.path.exists(link):
            os.symlink(os.path.basename(session_file), link)
    except OSError:
        pass


def wait_for_browser(monitor, poll=3.0):
    """Blocks cheaply until a browser process exists. Returns False if asked to stop first."""
    while not STOP["flag"]:
        if monitor.find_browser_pids():
            return True
        _interruptible_sleep(poll)
    return False


def watch(args):
    base = args.watch_dir
    os.makedirs(base, exist_ok=True)
    monitor = load_monitor_module()
    print(f"Watching for Bharat Browser sessions; JSON logs go to {base}", flush=True)
    while wait_for_browser(monitor):
        started = time.time()
        stamp = time.strftime("session-%Y%m%d-%H%M%S")
        print(f"[{time.strftime('%H:%M:%S')}] Browser detected, recording {stamp}", flush=True)
        run = collect(monitor, args.interval, None, 0, quiet=args.quiet)
        length = run.samples[-1]["t"] if run.samples else 0.0
        if length < args.min_session:
            print(f"[{time.strftime('%H:%M:%S')}] Session lasted {length:.0f}s (< {args.min_session:.0f}s); not reported.", flush=True)
            continue
        session_file = os.path.join(base, stamp + ".json")
        findings = finish_session(run, notify=args.notify, json_path=session_file, started_at=started)
        update_latest_link(base, session_file)
        removed = prune_sessions(base, args.keep)
        actionable = [f for f in findings if f["severity"] in ("HIGH", "MEDIUM")]
        print(f"[{time.strftime('%H:%M:%S')}] Session ended after {length / 60:.1f} min: "
              f"{len(actionable)} issue(s). Log: {session_file}"
              + (f" (pruned {len(removed)} old)" if removed else ""), flush=True)
    print("Stopping.", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Diagnose a running Bharat Browser and suggest improvements.")
    ap.add_argument("--interval", type=float, default=5.0, help="Seconds between samples (default: 5)")
    ap.add_argument("--duration", type=float, default=None, help="Stop after this many seconds (default: until Ctrl+C or browser exit)")
    ap.add_argument("--snapshot", action="store_true", help="Quick ~10s check (no leak analysis)")
    ap.add_argument("--wait", type=float, default=30.0, help="Seconds to wait for the browser to start (default: 30)")
    ap.add_argument("--output-dir", default=None, help="Directory for report.md and samples.csv (default: ./bharat-diagnostics-<timestamp>)")
    ap.add_argument("--watch", action="store_true", help="Diagnose every browser session, forever (waits for each launch)")
    ap.add_argument("--watch-dir", default=DEFAULT_WATCH_DIR, help="Where watch mode stores JSON session logs (default: <project>/logs)")
    ap.add_argument("--keep", type=int, default=50, help="Watch mode: keep this many most recent session logs (default: 50)")
    ap.add_argument("--min-session", type=float, default=30.0, help="Watch mode: skip reports for sessions shorter than this (default: 30s)")
    ap.add_argument("--notify", action="store_true", help="Desktop notification when a session has HIGH/MEDIUM findings")
    ap.add_argument("--quiet", action="store_true", help="No per-sample status lines")
    args = ap.parse_args()
    install_signal_handlers()

    if args.watch:
        return watch(args)
    if args.snapshot:
        args.duration, args.interval = 10.0, 2.0

    out_dir = args.output_dir or f"bharat-diagnostics-{int(time.time())}"
    print(f"Bharat Browser diagnostics -> {out_dir}  (Ctrl+C to stop and write the report)")
    run = collect(load_monitor_module(), args.interval, args.duration, args.wait, quiet=args.quiet)
    findings = finish_session(run, out_dir, notify=args.notify, json_path=os.path.join(out_dir, "session.json"), started_at=time.time() - (run.samples[-1]["t"] if run.samples else 0))

    print()
    for f in sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]]):
        if f["severity"] != "INFO":
            print(f"[{f['severity']}] {f['title']}")
    print(f"\nReport: {os.path.join(out_dir, 'report.md')}\nSamples: {os.path.join(out_dir, 'samples.csv')}")


if __name__ == "__main__":
    main()
