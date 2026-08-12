const { app, BrowserWindow, session, ipcMain } = require('electron');
const path = require('path');
const os = require('os');
const { isAdOrTracker } = require('./src/adblocker');
const { sanitizeUrl } = require('./src/clearurls');
const { getDarkReaderCSS } = require('./src/darkreader');
const downloadManager = require('./src/downloadManager');

// System Spec Optimization (Low Spec < 4GB RAM, High Spec >= 4GB RAM)
const TOTAL_RAM_GB = os.totalmem() / (1024 * 1024 * 1024);
const IS_LOW_SPEC = TOTAL_RAM_GB < 4;

if (IS_LOW_SPEC) {
  app.commandLine.appendSwitch('js-flags', '--max-old-space-size=512');
  app.commandLine.appendSwitch('disable-background-timer-throttling');
  app.commandLine.appendSwitch('renderer-process-limit', '2');
} else {
  app.commandLine.appendSwitch('enable-gpu-rasterization');
  app.commandLine.appendSwitch('enable-zero-copy');
  app.commandLine.appendSwitch('ignore-gpu-blocklist');
}

let mainWindow = null;
let sessionBlockedCount = 0;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 850,
    minWidth: 800,
    minHeight: 600,
    title: 'Bharat Browser v2.3.0',
    backgroundColor: '#030712',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webviewTag: true
    }
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  const webSession = session.defaultSession;

  // Modern Universal User Agent
  webSession.setUserAgent(
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 BharatBrowser/2.3.0'
  );

  // Integrated Download Manager Setup
  webSession.on('will-download', (event, item, webContents) => {
    downloadManager.addDownload(item, mainWindow.webContents);
  });

  // Web Request Interceptor (HTTPS Enforcement, ClearURLs, 2-Stage Ad Blocker)
  webSession.webRequest.onBeforeRequest((details, callback) => {
    let url = details.url;

    // Preserve main frame navigation for universal website compatibility
    if (details.resourceType === 'mainFrame') {
      // HTTPS Enforcement
      if (url.startsWith('http://') && !url.includes('localhost') && !url.includes('127.0.0.1')) {
        url = url.replace('http://', 'https://');
        return callback({ redirectURL: url });
      }
      return callback({ cancel: false });
    }

    // ClearURLs Parameter Sanitization
    const sanitized = sanitizeUrl(url);
    if (sanitized && sanitized !== url) {
      url = sanitized;
    }

    // 2-Stage Ad & Tracker Blocker
    if (isAdOrTracker(url)) {
      sessionBlockedCount++;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('tracker-blocked-event', { count: sessionBlockedCount, url });
      }
      return callback({ cancel: true });
    }

    if (url !== details.url) {
      return callback({ redirectURL: url });
    }

    return callback({ cancel: false });
  });

  // Security Headers Enforcement
  webSession.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = Object.assign({}, details.responseHeaders);

    if (!responseHeaders['X-Content-Type-Options'] && !responseHeaders['x-content-type-options']) {
      responseHeaders['X-Content-Type-Options'] = ['nosniff'];
    }
    if (!responseHeaders['Referrer-Policy'] && !responseHeaders['referrer-policy']) {
      responseHeaders['Referrer-Policy'] = ['strict-origin-when-cross-origin'];
    }

    callback({ responseHeaders });
  });
}

// IPC Handlers
ipcMain.handle('get-system-specs', () => {
  return {
    totalRamGb: TOTAL_RAM_GB.toFixed(1),
    isLowSpec: IS_LOW_SPEC,
    version: '2.3.0'
  };
});

ipcMain.handle('get-shield-stats', () => {
  return { sessionBlockedCount };
});

ipcMain.handle('get-darkreader-css', () => {
  return getDarkReaderCSS();
});

ipcMain.handle('get-downloads', () => {
  return downloadManager.getDownloads();
});

ipcMain.handle('clear-browsing-data', async () => {
  try {
    const webSession = session.defaultSession;
    await webSession.clearStorageData({
      storages: ['cookies', 'filesystem', 'indexdb', 'localstorage', 'shadercache', 'websql', 'serviceworkers', 'cachestorage']
    });
    await webSession.clearCache();
    return { success: true };
  } catch (e) {
    console.error('Error clearing data:', e);
    return { success: false, error: e.message };
  }
});

ipcMain.handle('save-screenshot', async (event, dataUrl) => {
  try {
    const desktopDir = path.join(os.homedir(), 'Desktop');
    if (!fs.existsSync(desktopDir)) {
      fs.mkdirSync(desktopDir, { recursive: true });
    }
    const timestamp = new Date().toISOString().replace(/[-:T.]/g, '').slice(0, 15);
    const filename = `BharatScreenshot_${timestamp}.jpeg`;
    const filepath = path.join(desktopDir, filename);

    const base64Data = dataUrl.replace(/^data:image\/jpeg;base64,/, '').replace(/^data:image\/png;base64,/, '');
    fs.writeFileSync(filepath, Buffer.from(base64Data, 'base64'));

    return { success: true, filepath, filename };
  } catch (e) {
    console.error('Error saving screenshot:', e);
    return { success: false, error: e.message };
  }
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
