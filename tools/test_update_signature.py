#!/usr/bin/env python3
"""Tests for the updater's Ed25519 verification (bharat_browser.py) against RFC 8032
vectors and the `cryptography` package, which signs exactly like tools/sign-release.py."""
import hashlib
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = open(os.path.join(ROOT, "bharat_browser.py")).read()

# Load only the verification code; importing the whole browser needs GTK/WebKit.
ns = {"hashlib": hashlib}
exec(re.search(r"# ed25519 verify begin\n(.*?)# ed25519 verify end", SRC, re.S).group(1), ns)
verify = ns["ed25519_verify"]

RFC8032 = [  # (public key, message, signature) from RFC 8032 section 7.1
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


class VerifyTests(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for pub, msg, sig in RFC8032:
            self.assertTrue(verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig)))

    def test_rfc8032_rejects_tampering(self):
        pub, msg, sig = (bytes.fromhex(x) for x in RFC8032[1])
        self.assertFalse(verify(pub, msg + b"x", sig))
        self.assertFalse(verify(pub, msg, bytes([sig[0] ^ 1]) + sig[1:]))
        self.assertFalse(verify(bytes.fromhex(RFC8032[0][0]), msg, sig))

    def test_malformed_inputs(self):
        pub, msg, sig = (bytes.fromhex(x) for x in RFC8032[1])
        self.assertFalse(verify(pub[:31], msg, sig))
        self.assertFalse(verify(pub, msg, sig[:63]))
        self.assertFalse(verify(pub, msg, b"\x00" * 64))
        self.assertFalse(verify(b"\xff" * 32, msg, sig))
        # s >= group order (malleability) must be rejected
        l = 2 ** 252 + 27742317777372353535851937790883648493
        bad_s = (int.from_bytes(sig[32:], "little") + l).to_bytes(32, "little")
        self.assertFalse(verify(pub, msg, sig[:32] + bad_s))


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            raise unittest.SkipTest("cryptography not installed")
        cls.key = Ed25519PrivateKey.generate()
        cls.pub = cls.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ns.update(UPDATE_PUBLIC_KEY_HEX=cls.pub.hex(), _UPDATE_SIGNATURE_PREFIX=b"bharat-browser-update\n")
        exec(re.search(r"(def verify_update_signature.*?)\n\n\n", SRC, re.S).group(1), ns)

    def sign(self, version, source):
        return self.key.sign(b"bharat-browser-update\n" + version.encode() + b"\n" + source).hex()

    def test_matches_cryptography(self):
        for n in (0, 1, 100, 5000):
            data = os.urandom(n)
            self.assertTrue(verify(self.pub, data, self.key.sign(data)))

    def test_release_signature(self):
        src = b"print('hello')\n"
        sig = self.sign("1.2.3", src)
        v = ns["verify_update_signature"]
        self.assertTrue(v("1.2.3", src, sig))
        self.assertFalse(v("1.2.4", src, sig), "signature must be bound to the version")
        self.assertFalse(v("1.2.3", src + b"#", sig), "tampered source")
        self.assertFalse(v("1.2.3", src, ""), "missing signature")
        self.assertFalse(v("1.2.3", src, "zz"), "non-hex signature")
        self.assertFalse(v("1.2.3", src, sig[:-2]), "truncated signature")

    def test_wrong_key_rejected(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        other = Ed25519PrivateKey.generate().sign(b"bharat-browser-update\n1.2.3\nx").hex()
        self.assertFalse(ns["verify_update_signature"]("1.2.3", b"x", other))


if __name__ == "__main__":
    unittest.main()
