#!/bin/bash
# Keep the "sha256" in package.json equal to the hash of bharat_browser.py.
# The in-app updater downloads bharat_browser.py for the announced version and
# refuses to install it unless the bytes match this value, so it must be
# refreshed whenever bharat_browser.py changes (the build scripts call this).
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$ROOT" <<'PY'
import hashlib, json, os, sys
root = sys.argv[1]
src = os.path.join(root, "bharat_browser.py")
pkg = os.path.join(root, "package.json")
digest = hashlib.sha256(open(src, "rb").read()).hexdigest()
data = json.load(open(pkg))
if data.get("sha256") == digest:
    print(f"package.json sha256 already current ({digest[:12]}...)")
else:
    data["sha256"] = digest
    with open(pkg, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"package.json sha256 updated to {digest}")
PY
