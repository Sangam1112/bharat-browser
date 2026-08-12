const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf-8'));
const VERSION = pkg.version;
const BUILD_DIR = `/tmp/bharat-deb/bharat-browser_${VERSION}_amd64`;
const DEB_OUTPUT = path.join(__dirname, `bharat-browser_${VERSION}_amd64.deb`);

console.log(`🇮🇳 Building Bharat Browser v${VERSION} .deb package for Ubuntu...`);

// Clean & Create Directories
execSync(`rm -rf ${BUILD_DIR}`);
fs.mkdirSync(path.join(BUILD_DIR, 'DEBIAN'), { recursive: true });
fs.mkdirSync(path.join(BUILD_DIR, 'usr/bin'), { recursive: true });
fs.mkdirSync(path.join(BUILD_DIR, 'usr/share/applications'), { recursive: true });
fs.mkdirSync(path.join(BUILD_DIR, 'usr/share/bharat-browser'), { recursive: true });

// 1. DEBIAN/control
const controlContent = `Package: bharat-browser
Version: ${VERSION}
Section: web
Priority: optional
Architecture: amd64
Maintainer: Bharat Browser Team <support@bharat-browser.org>
Depends: nodejs, libgtk-3-0, libnss3, libasound2
Description: Bharat Browser - Modern Privacy Web Browser (Made in India)
 Universal compatibility browser with DarkReader engine, uBlock & Privacy Badger filter, ClearURLs, and integrated Download Manager.
`;
fs.writeFileSync(path.join(BUILD_DIR, 'DEBIAN/control'), controlContent);

// 2. /usr/bin/bharat-browser launcher
const launcherContent = `#!/bin/bash
# Bharat Browser v${VERSION} Launcher
npx electron /usr/share/bharat-browser "$@"
`;
const binPath = path.join(BUILD_DIR, 'usr/bin/bharat-browser');
fs.writeFileSync(binPath, launcherContent);
fs.chmodSync(binPath, 0o755);

// 3. /usr/share/applications/bharat-browser.desktop
const desktopContent = `[Desktop Entry]
Name=Bharat Browser
Comment=Modern Privacy Web Browser (Made in India)
Exec=/usr/bin/bharat-browser %U
Icon=web-browser
Terminal=false
Type=Application
Categories=Network;WebBrowser;
`;
fs.writeFileSync(
  path.join(BUILD_DIR, 'usr/share/applications/bharat-browser.desktop'),
  desktopContent
);

// 4. Copy Application Source Files
const filesToCopy = ['package.json', 'main.js', 'preload.js'];
filesToCopy.forEach(file => {
  fs.copyFileSync(path.join(__dirname, file), path.join(BUILD_DIR, 'usr/share/bharat-browser', file));
});

// Copy src, renderer & node_modules
execSync(`cp -r ${path.join(__dirname, 'src')} ${path.join(BUILD_DIR, 'usr/share/bharat-browser/')}`);
execSync(`cp -r ${path.join(__dirname, 'renderer')} ${path.join(BUILD_DIR, 'usr/share/bharat-browser/')}`);
if (fs.existsSync(path.join(__dirname, 'node_modules'))) {
  execSync(`cp -rL ${path.join(__dirname, 'node_modules')} ${path.join(BUILD_DIR, 'usr/share/bharat-browser/')}`);
  execSync(`find ${BUILD_DIR} -type l -delete || true`);
}

// 5. Build .deb with dpkg-deb
execSync(`dpkg-deb --root-owner-group --build ${BUILD_DIR} ${DEB_OUTPUT}`);
console.log(`✅ Successfully generated versioned .deb package: ${DEB_OUTPUT}`);
