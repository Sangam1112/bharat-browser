#!/usr/bin/env python3
"""Fail if the version differs between package.json (the single source of truth),
bharat_browser.py, the README badge, CHANGELOG.md and the RPM changelog."""
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


version = json.loads(read("package.json"))["version"]
checks = {
    "bharat_browser.py APP_VERSION": re.search(r'^APP_VERSION = "([^"]+)"', read("bharat_browser.py"), re.M),
    "bharat_browser.py header": re.search(r"^Bharat Browser v(\S+) - ", read("bharat_browser.py"), re.M),
    "README badge": re.search(r"badge/version-([\d.]+)-blue", read("README.md")),
    "CHANGELOG.md top entry": re.search(r"^## \[?v?([\d.]+)", read("CHANGELOG.md"), re.M),
    "build-rpm.sh top changelog": re.search(r"^%changelog\n\* .*? - ([\d.]+)-1", read("build-rpm.sh"), re.M),
}
bad = [f"  {name}: {m.group(1) if m else 'NOT FOUND'}" for name, m in checks.items() if not m or m.group(1) != version]
if bad:
    sys.exit(f"Version mismatch (package.json says {version}):\n" + "\n".join(bad) + "\nRun tools/bump-version.py to change versions.")
print(f"Version {version} is consistent everywhere.")
