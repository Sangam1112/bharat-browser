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

    # ---- Red Hat family: WebKit2GTK is "webkit2gtk4.1" on Fedora, "webkit2gtk3" on RHEL/Rocky/Alma ----
    def _stateful_rpm_dnf(self, installed, dnf_fails=()):
        """rpm -q answers from a list that `dnf install` appends to; chosen packages make dnf fail."""
        state = os.path.join(self.tmp, "installed")
        with open(state, "w") as f:
            f.write("\n".join(installed) + "\n")
        log = os.path.join(self.tmp, "dnf.log")
        self._stub("rpm", f'#!/bin/sh\ngrep -qx "$2" {state}\n')
        self._stub("dnf", f'''#!/bin/sh
echo "$@" >> {log}
shift; shift   # "install" "-y"
for pkg in "$@"; do
    case " {" ".join(dnf_fails)} " in *" $pkg "*) exit 1;; esac
    echo "$pkg" >> {state}
done
''')
        return log

    BASE = ("python3", "python3-gobject", "gtk3")

    def test_fedora_installer_accepts_rhel_style_webkit_package(self):
        self._stateful_rpm_dnf(self.BASE + ("webkit2gtk3",))
        home, result = self.run_installer(ROOT, "install-fedora.sh")
        self.assert_installed(home, result)
        self.assertIn("WebKit2GTK: webkit2gtk3", result.stdout)

    def test_fedora_installer_falls_back_to_webkit2gtk3_when_4_1_is_unavailable(self):
        log = self._stateful_rpm_dnf(self.BASE, dnf_fails=("webkit2gtk4.1",))
        home, result = self.run_installer(ROOT, "install-fedora.sh")
        self.assert_installed(home, result)
        with open(log) as f:
            tried = f.read()
        self.assertLess(tried.index("webkit2gtk4.1"), tried.index("webkit2gtk3"), "tries Fedora's name first")

    def test_fedora_installer_prefers_webkit2gtk4_1_and_installs_missing_base_packages(self):
        log = self._stateful_rpm_dnf(("python3",))
        home, result = self.run_installer(ROOT, "install-fedora.sh")
        self.assert_installed(home, result)
        with open(log) as f:
            tried = f.read()
        self.assertIn("python3-gobject", tried)
        self.assertNotIn("webkit2gtk3", tried, "no fallback needed when 4.1 installs fine")

    def test_fedora_installer_explains_both_names_when_nothing_works(self):
        self._stateful_rpm_dnf(self.BASE, dnf_fails=("webkit2gtk4.1", "webkit2gtk3"))
        home, result = self.run_installer(ROOT, "install-fedora.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("webkit2gtk4.1", result.stderr)
        self.assertIn("webkit2gtk3", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(home, ".local", "bin", "bharat-browser")), "nothing half-installed")

    def test_rpm_accepts_either_webkit_package(self):
        rpms = sorted(glob.glob(os.path.join(ROOT, "bharat-browser-*.noarch.rpm")))
        if not rpms or not shutil.which("rpm"):
            self.skipTest("no RPM built, or the rpm tool is missing")
        out = subprocess.run(["rpm", "-qp", "--requires", rpms[-1]], capture_output=True, text=True).stdout
        self.assertIn("webkit2gtk4.1", out)
        self.assertIn("webkit2gtk3", out)

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
