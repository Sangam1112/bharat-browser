#!/usr/bin/env python3
"""The install scripts must actually install: run each one with the system tools
(sudo, apt, dnf/rpm) stubbed out and a throw-away HOME, then check the result.
Also checks the Fedora archive, because it has to work when extracted on its own."""
import glob
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for name in ("apt-get", "apt-cache", "update-desktop-database", "rpm"):
            self._stub(name, "#!/bin/sh\nexit 0\n")
        self._stub("sudo", '#!/bin/sh\nexec "$@"\n')

    def _stub(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def run_installer(self, workdir, script):
        home = os.path.join(self.tmp, "home-" + script.replace(".", "-"))
        env = dict(os.environ, HOME=home, PATH=self.bin + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", os.path.join(workdir, script)], cwd=workdir, env=env,
                                capture_output=True, text=True, timeout=60)
        return home, result

    def assert_installed(self, home, result, desktop=True):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        app = os.path.join(home, ".local", "share", "bharat-browser")
        launcher = os.path.join(home, ".local", "bin", "bharat-browser")
        self.assertTrue(os.path.isfile(os.path.join(app, "bharat_browser.py")))
        self.assertTrue(os.path.isfile(os.path.join(app, "assets", "bharat_icon.png")))
        self.assertTrue(os.access(launcher, os.X_OK))
        if desktop:
            self.assertTrue(os.path.isfile(os.path.join(home, ".local", "share", "applications", "bharat-browser.desktop")))
        with open(os.path.join(app, "bharat_browser.py"), "rb") as a, open(os.path.join(ROOT, "bharat_browser.py"), "rb") as b:
            self.assertEqual(a.read(), b.read(), "installed the current bharat_browser.py")

    def test_ubuntu_from_checkout(self):
        self.assert_installed(*self.run_installer(ROOT, "install-ubuntu.sh"))

    def test_fedora_from_checkout(self):
        self.assert_installed(*self.run_installer(ROOT, "install-fedora.sh"))

    def test_wsl_from_checkout(self):
        self.assert_installed(*self.run_installer(ROOT, "install-wsl.sh"), desktop=False)

    def test_fedora_archive_works_when_extracted_alone(self):
        archives = sorted(glob.glob(os.path.join(ROOT, "bharat-browser_*_fedora.tar.gz")))
        if not archives:
            self.skipTest("build-rpm.sh hasn't been run, so there is no Fedora archive")
        extract = os.path.join(self.tmp, "extracted")
        os.makedirs(extract)
        with tarfile.open(archives[-1]) as tar:
            tar.extractall(extract)
        self.assert_installed(*self.run_installer(extract, "install-fedora.sh"))


if __name__ == "__main__":
    unittest.main()
