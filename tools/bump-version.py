#!/usr/bin/env python3
"""Bump the release version everywhere in one go.

  tools/bump-version.py 1.4.1 "One-line summary of the changes"

Updates package.json, bharat_browser.py (header + APP_VERSION), the README
(badge and install examples), CHANGELOG.md and the RPM changelog. The build
scripts read the version from package.json, so nothing else needs editing.
Run the build scripts afterwards: they refresh the checksum and sign the release.
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def path(name):
    return os.path.join(ROOT, name)


def read(name):
    with open(path(name), encoding="utf-8") as f:
        return f.read()


def write(name, text):
    with open(path(name), "w", encoding="utf-8") as f:
        f.write(text)


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit(__doc__)
    new, summary = sys.argv[1], sys.argv[2].strip()
    pkg = json.loads(read("package.json"))
    old = pkg["version"]
    if new == old:
        sys.exit(f"Already at {old}")

    pkg["version"] = new
    write("package.json", json.dumps(pkg, indent=2) + "\n")

    py = read("bharat_browser.py")
    py = py.replace(f"Bharat Browser v{old} - ", f"Bharat Browser v{new} - ", 1)
    py = py.replace(f'APP_VERSION = "{old}"', f'APP_VERSION = "{new}"', 1)
    write("bharat_browser.py", py)

    write("README.md", read("README.md").replace(old, new))

    now = datetime.datetime.now()
    rpm = read("build-rpm.sh")
    entry = f"* {now.strftime('%a %b %d %Y')} Bharat Browser Developer <developer@bharatbrowser.org> - {new}-1\n- {summary}\n"
    rpm = rpm.replace("%changelog\n", "%changelog\n" + entry, 1)
    write("build-rpm.sh", rpm)

    log = read("CHANGELOG.md")
    section = f"## [{new}] - {now.strftime('%Y-%m-%d')}\n\n- {summary}\n\n"
    first = re.search(r"^## ", log, re.M)
    write("CHANGELOG.md", log[:first.start()] + section + log[first.start():] if first else log + "\n" + section)

    print(f"Bumped {old} -> {new}. Edit CHANGELOG.md to expand the notes, then run build-deb.sh and build-rpm.sh.")


if __name__ == "__main__":
    main()
