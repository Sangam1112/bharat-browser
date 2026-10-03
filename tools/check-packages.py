#!/usr/bin/env python3
"""Verify that the built .deb, .rpm and Fedora archive all carry the version in
package.json: the package metadata AND the bharat_browser.py inside each one, which
must also be byte-identical to the one in the repository (the signed file).

  tools/check-packages.py
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.chdir(ROOT)
with open("package.json") as f:
    VERSION = json.load(f)["version"]
with open("bharat_browser.py", "rb") as f:
    SOURCE = f.read()
SOURCE_SHA = hashlib.sha256(SOURCE).hexdigest()
APP = "usr/share/bharat-browser/bharat_browser.py"
problems = []


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw).stdout


def check_app(label, data):
    m = re.search(rb'^APP_VERSION = "([^"]+)"', data, re.M)
    found = m.group(1).decode() if m else "NOT FOUND"
    if found != VERSION:
        problems.append(f"{label}: embedded app is {found}, expected {VERSION}")
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        problems.append(f"{label}: embedded bharat_browser.py differs from the repository's (signed) file")


def deb():
    name = f"bharat-browser_{VERSION}-1_all.deb"
    if not os.path.exists(name):
        return problems.append(f"missing {name}")
    members = {}
    out = run(["ar", "t", name]).decode().split()
    for member in out:
        members[member] = run(["ar", "p", name, member])
    control = next((v for k, v in members.items() if k.startswith("control.tar")), b"")
    with tarfile.open(fileobj=io.BytesIO(control)) as t:
        text = t.extractfile("./control").read().decode()
    version = re.search(r"^Version: (\S+)", text, re.M)
    if not version or version.group(1) != VERSION:
        problems.append(f"{name}: control Version is {version.group(1) if version else 'NOT FOUND'}")
    data = next((v for k, v in members.items() if k.startswith("data.tar")), b"")
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        check_app(name, t.extractfile("./" + APP).read())


def rpm():
    name = f"bharat-browser-{VERSION}-1.noarch.rpm"
    if not os.path.exists(name):
        return problems.append(f"missing {name}")
    header = run(["rpm", "-qp", "--queryformat", "%{VERSION}-%{RELEASE}", name]).decode()
    if header != f"{VERSION}-1":
        problems.append(f"{name}: header says {header}")
    cpio = subprocess.run(f"rpm2cpio '{name}' | cpio -i --to-stdout ./{APP} 2>/dev/null", shell=True, capture_output=True).stdout
    check_app(name, cpio)


def archive():
    name = f"bharat-browser_{VERSION}_fedora.tar.gz"
    if not os.path.exists(name):
        return problems.append(f"missing {name}")
    with tarfile.open(name) as t:
        for member in ("./bharat_browser.py", "./" + APP):
            check_app(f"{name}:{member}", t.extractfile(member).read())


for check in (deb, rpm, archive):
    check()
if problems:
    sys.exit("Package check FAILED:\n  " + "\n  ".join(problems))
print(f"All three packages are version {VERSION} and contain the signed bharat_browser.py ({SOURCE_SHA[:12]}).")
