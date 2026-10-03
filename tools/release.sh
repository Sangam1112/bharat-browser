#!/bin/bash
# Publish the version in package.json: push master and the vX.Y.Z tag (the in-app
# updater downloads from that tag), verify the published release the way the updater
# will, and create a GitHub Release with the packages attached.
# Prerequisites: changes committed, packages built (build-deb.sh, build-rpm.sh), `gh auth login` done.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VERSION="$(python3 -c 'import json; print(json.load(open("package.json"))["version"])')"
TAG="v${VERSION}"
PKGS=("bharat-browser_${VERSION}-1_all.deb" "bharat-browser-${VERSION}-1.noarch.rpm" "bharat-browser_${VERSION}_fedora.tar.gz")

python3 tools/check-version.py
python3 tools/sign-release.py verify
for f in "${PKGS[@]}"; do [ -f "$f" ] || { echo "Missing $f: run build-deb.sh and build-rpm.sh first"; exit 1; }; done
if ! git diff --quiet || ! git diff --cached --quiet; then echo "Commit your changes first."; exit 1; fi
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then echo "Tag ${TAG} already exists."; exit 1; fi

echo "Publishing ${TAG}..."
git push origin master
git tag "${TAG}"
git push origin "${TAG}"
python3 tools/verify-published.py "${VERSION}"

NOTES="$(python3 - "$VERSION" <<'PY'
import re, sys
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(r"^## \[%s\][^\n]*\n(.*?)(?=^## |\Z)" % re.escape(sys.argv[1]), text, re.S | re.M)
print((m.group(1).strip() if m else f"Bharat Browser {sys.argv[1]}"))
PY
)"
gh release create "${TAG}" --title "Bharat Browser ${VERSION}" --notes "${NOTES}" "${PKGS[@]}"
echo "Done: https://github.com/Sangam1112/bharat-browser/releases/tag/${TAG}"
