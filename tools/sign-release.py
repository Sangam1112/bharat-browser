#!/usr/bin/env python3
"""Sign / verify the auto-update payload with Ed25519.

The in-app updater (bharat_browser.py) refuses to install an update unless
package.json carries a "signature" that verifies against UPDATE_PUBLIC_KEY_HEX
baked into the installed copy. The private key never lives in the repo.

  sign-release.py keygen   create the release key (once) and print the public key
  sign-release.py sign     sign bharat_browser.py + package.json "version"
  sign-release.py verify   check package.json's signature (independent
                           implementation: needs the `cryptography` package)

Signed message = b"bharat-browser-update\\n" + version + b"\\n" + file bytes,
so a validly signed older file cannot be passed off as a newer version.
Requires the `cryptography` package (release machine only, not end users).
"""
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(ROOT, "bharat_browser.py")
PKG = os.path.join(ROOT, "package.json")
KEY_PATH = os.environ.get(
    "BHARAT_SIGNING_KEY",
    os.path.expanduser("~/.config/bharat-browser-signing/release-ed25519.pem"),
)
PREFIX = b"bharat-browser-update\n"


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        return serialization, Ed25519PrivateKey, Ed25519PublicKey
    except ImportError:
        sys.exit("The 'cryptography' package is required (pip install cryptography / python3-cryptography).")


def message(version, source):
    return PREFIX + version.encode() + b"\n" + source


def public_hex(private_key, serialization):
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw.hex()


def load_pkg():
    with open(PKG) as f:
        return json.load(f)


def save_pkg(data):
    with open(PKG, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def cmd_keygen():
    serialization, Ed25519PrivateKey, _ = _crypto()
    if os.path.exists(KEY_PATH):
        sys.exit(f"Refusing to overwrite existing key {KEY_PATH}")
    os.makedirs(os.path.dirname(KEY_PATH), mode=0o700, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    print(f"Private key written to {KEY_PATH} (mode 600). BACK IT UP and never commit it.")
    print(f"Public key (put in UPDATE_PUBLIC_KEY_HEX): {public_hex(key, serialization)}")


def cmd_sign():
    data = load_pkg()
    data.pop("signature", None)  # a stale signature is worse than none
    if not os.path.exists(KEY_PATH):
        save_pkg(data)
        print(f"No signing key at {KEY_PATH}: package.json has NO signature, so the updater will reject this release.", file=sys.stderr)
        sys.exit(2)
    serialization, _, _ = _crypto()
    key = serialization.load_pem_private_key(open(KEY_PATH, "rb").read(), password=None)
    with open(SRC, "rb") as f:
        source = f.read()
    data["signature"] = key.sign(message(data["version"], source)).hex()
    save_pkg(data)
    print(f"Signed v{data['version']} ({data['signature'][:16]}...)")


def cmd_verify():
    serialization, _, Ed25519PublicKey = _crypto()
    from cryptography.exceptions import InvalidSignature
    with open(SRC, "rb") as f:
        source = f.read()
    m = re.search(r'^UPDATE_PUBLIC_KEY_HEX\s*=\s*"([0-9a-f]{64})"', source.decode(), re.M)
    if not m:
        sys.exit("UPDATE_PUBLIC_KEY_HEX not found in bharat_browser.py")
    data = load_pkg()
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(m.group(1))).verify(
            bytes.fromhex(data.get("signature", "")), message(data["version"], source))
    except (InvalidSignature, ValueError):
        sys.exit("INVALID: package.json signature does not match bharat_browser.py + version")
    print(f"OK: signature valid for v{data['version']}")


if __name__ == "__main__":
    cmds = {"keygen": cmd_keygen, "sign": cmd_sign, "verify": cmd_verify}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
