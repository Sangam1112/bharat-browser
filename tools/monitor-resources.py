#!/usr/bin/env python3
"""
Standalone process/memory monitor for a running Bharat Browser instance.

Not part of the shipped packages (RPM/.deb/tarball) — a diagnostic tool for
developers reproducing reports like memory growth or "system overload" on a
real machine, without needing to install anything beyond the Python 3
standard library.

Tracks the browser's whole process group by matching command lines: the main
`bharat_browser.py` process plus every WebKit subprocess it spawns (one
WebProcess roughly per site/tab group, a shared NetworkProcess, and a GPU
process if one is running) — found by pattern rather than by walking process
parent/child links, since WebKit's sandboxing can reparent subprocesses under
an intermediate wrapper (e.g. bubblewrap) rather than keeping them as direct
children of the launching process.

Usage:
    python3 tools/monitor-resources.py                  # sample every 2s until Ctrl+C
    python3 tools/monitor-resources.py --interval 5
    python3 tools/monitor-resources.py --output run.csv
    python3 tools/monitor-resources.py --duration 300    # stop automatically after 5 minutes

Output: a live summary line per sample on stdout, and a full CSV log (one
row per process per sample) for later analysis/graphing. On exit (Ctrl+C or
--duration elapsed), prints a run summary: peak and average total RSS.

Caveat: summed RSS across processes overstates true unique memory usage,
since it double-counts shared library pages mapped into multiple processes
(this is the standard, simple approximation most lightweight monitors use —
for exact unique/proportional memory you'd want PSS from /proc/<pid>/smaps_rollup,
which requires root on some kernels and is far more expensive to sample).
"""
import argparse
import csv
import os
import re
import signal
import sys
import time

ROLE_EXECUTABLE_NAMES = {
    "web_process": "WebKitWebProcess",
    "network_process": "WebKitNetworkProcess",
    "gpu_process": "WebKitGPUProcess",
}

CLOCK_TICKS_PER_SEC = os.sysconf("SC_CLK_TCK")


def read_cmdline_tokens(pid):
    """Returns the process's argv as a list of tokens (NUL-delimited, as the
    kernel actually reports it) rather than a single joined string. This
    matters for correctly identifying the main process: a shell wrapper that
    merely *mentions* "bharat_browser.py" somewhere inside one large embedded
    command string (e.g. `bash -c "... python3 bharat_browser.py ..."`) would
    still match a naive substring search on the joined string, but does NOT
    have "bharat_browser.py" as its own standalone argv token the way the
    real `python3 bharat_browser.py` invocation does — confirmed live: an
    early version of this script misidentified exactly such a wrapper as a
    second main-process instance."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        if not raw:
            return []
        return [t.decode("utf-8", errors="replace") for t in raw.split(b"\0") if t]
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return []


def _token_names(name):
    """A token identifies process `name` if it equals it exactly (argv[0]
    with no path, e.g. just "bharat_browser.py") or ends with "/"+name (a
    full path, e.g. ".../webkit2gtk-4.1/WebKitWebProcess")."""
    def matches(token):
        return token == name or token.endswith("/" + name)
    return matches


def classify(tokens):
    """Every role requires a whole argv token to match a real executable
    name/path — never a substring search across the joined command line.
    Confirmed live: a substring search misclassified an unrelated shell
    process as a WebKit subprocess merely because one of its arguments
    happened to mention "WebKitWebProcess" in passing (a diagnostic pgrep
    command run earlier, still lingering as a background shell job) — the
    exact same class of false positive as the "main" process check below,
    just not yet applied to these roles in an earlier version."""
    if not tokens:
        return None
    for role, exe_name in ROLE_EXECUTABLE_NAMES.items():
        if any(_token_names(exe_name)(t) for t in tokens):
            return role
    if any(_token_names("bharat_browser.py")(t) for t in tokens):
        return "main"
    return None


def find_browser_pids():
    """Returns {pid: role} for every process belonging to a running Bharat
    Browser instance (main process + WebKit subprocesses)."""
    found = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        tokens = read_cmdline_tokens(pid)
        role = classify(tokens)
        if role:
            found[pid] = role
    return found


def read_proc_stats(pid):
    """Returns (rss_kb, utime_ticks, stime_ticks) or None if the process is
    gone (it may have exited between listing pids and sampling it)."""
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            status = f.read()
        m = re.search(r"^VmRSS:\s+(\d+) kB", status, re.MULTILINE)
        rss_kb = int(m.group(1)) if m else 0

        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read()
        # Command name field can contain spaces/parens, so split from the
        # last ')' rather than by whitespace position.
        after_comm = stat.rsplit(")", 1)[1].split()
        utime = int(after_comm[11])  # field 14, 0-indexed from field 3 (state)
        stime = int(after_comm[12])  # field 15
        return rss_kb, utime, stime
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
        return None


def format_mb(kb):
    return f"{kb / 1024:.1f}"


def main():
    parser = argparse.ArgumentParser(description="Monitor a running Bharat Browser's process/memory footprint.")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between samples (default: 2)")
    parser.add_argument("--duration", type=float, default=None, help="Stop automatically after this many seconds (default: run until Ctrl+C)")
    parser.add_argument("--output", default=None, help="CSV log file path (default: bharat-browser-monitor-<timestamp>.csv in the current directory)")
    args = parser.parse_args()

    output_path = args.output or f"bharat-browser-monitor-{int(time.time())}.csv"
    csv_file = open(output_path, "w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow(["timestamp", "elapsed_s", "pid", "role", "rss_mb", "cpu_percent"])

    print(f"Bharat Browser resource monitor — logging to {output_path}")
    print("Waiting for a running instance (launch it now if it isn't already running)...")

    prev_cpu = {}  # pid -> (utime, stime, wall_time)
    total_rss_samples = []
    start_time = time.time()
    stop = {"flag": False}

    def handle_sigint(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGINT, handle_sigint)

    saw_process_yet = False

    while not stop["flag"]:
        tick_start = time.time()
        pids = find_browser_pids()

        if not pids:
            if saw_process_yet:
                print(f"[{time.strftime('%H:%M:%S')}] Bharat Browser is no longer running. Stopping.")
                break
            time.sleep(min(args.interval, 1.0))
            if args.duration and (time.time() - start_time) >= args.duration:
                break
            continue

        saw_process_yet = True
        rows = []
        total_rss_kb = 0
        total_cpu = 0.0
        now = time.time()

        for pid, role in sorted(pids.items(), key=lambda kv: kv[1]):
            stats = read_proc_stats(pid)
            if stats is None:
                continue
            rss_kb, utime, stime = stats
            total_rss_kb += rss_kb

            cpu_percent = 0.0
            if pid in prev_cpu:
                prev_utime, prev_stime, prev_wall = prev_cpu[pid]
                wall_delta = now - prev_wall
                cpu_delta_ticks = (utime + stime) - (prev_utime + prev_stime)
                if wall_delta > 0:
                    cpu_percent = (cpu_delta_ticks / CLOCK_TICKS_PER_SEC) / wall_delta * 100.0
            prev_cpu[pid] = (utime, stime, now)
            total_cpu += cpu_percent

            rows.append((pid, role, rss_kb, cpu_percent))

        elapsed = now - start_time
        total_rss_samples.append(total_rss_kb)
        for pid, role, rss_kb, cpu_percent in rows:
            writer.writerow([f"{now:.1f}", f"{elapsed:.1f}", pid, role, format_mb(rss_kb), f"{cpu_percent:.1f}"])
        csv_file.flush()

        by_role = {}
        for _, role, rss_kb, _ in rows:
            by_role.setdefault(role, [0, 0.0])
            by_role[role][0] += 1
        breakdown = ", ".join(f"{role}x{count}" for role, (count, _) in sorted(by_role.items()))
        print(f"[{time.strftime('%H:%M:%S')}] total_rss={format_mb(total_rss_kb)}MB "
              f"total_cpu={total_cpu:.1f}% procs=({breakdown})")

        if args.duration and elapsed >= args.duration:
            break

        sleep_for = args.interval - (time.time() - tick_start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    csv_file.close()

    print()
    print(f"Log written to {output_path}")
    if total_rss_samples:
        peak = max(total_rss_samples)
        avg = sum(total_rss_samples) / len(total_rss_samples)
        print(f"Samples: {len(total_rss_samples)}  Peak total RSS: {format_mb(peak)}MB  Average total RSS: {format_mb(avg)}MB")
    else:
        print("No samples were collected (Bharat Browser was never observed running).")


if __name__ == "__main__":
    main()
