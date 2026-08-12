let tabs = [];
let activeTabId = null;
let tabCounter = 0;
let darkReaderActive = false;

const tabsContainer = document.getElementById('tabs-container');
const webviewsContainer = document.getElementById('webviews-container');
const btnNewTab = document.getElementById('btn-new-tab');

const btnBack = document.getElementById('btn-back');
const btnForward = document.getElementById('btn-forward');
const btnReload = document.getElementById('btn-reload');
const btnHome = document.getElementById('btn-home');
const urlInput = document.getElementById('url-input');

const chkDarkReader = document.getElementById('chk-darkreader');
const btnShield = document.getElementById('btn-shield');
const shieldCountEl = document.getElementById('shield-count');
const btnOpenDownloads = document.getElementById('btn-open-downloads');
const downloadCountEl = document.getElementById('download-count');
const downloadDrawer = document.getElementById('download-drawer');
const downloadList = document.getElementById('download-list');
const closeDrawer = document.getElementById('close-drawer');

const hardwareBadge = document.getElementById('hardware-badge');
const specModeText = document.getElementById('spec-mode-text');
const statusText = document.getElementById('status-text');
const systemSpecInfo = document.getElementById('system-spec-info');

const btnSettings = document.getElementById('btn-settings');
const settingsModal = document.getElementById('settings-modal');
const closeModal = document.getElementById('close-modal');

// Default Homepage
const DEFAULT_HOMEPAGE = 'https://www.google.co.in';

// Initialize Specs
async function initSystemSpecs() {
  try {
    if (window.bharatAPI) {
      const specs = await window.bharatAPI.getSystemSpecs();
      systemSpecInfo.textContent = `System RAM: ${specs.totalRamGb} GB | v${specs.version}`;
      if (specs.isLowSpec) {
        specModeText.textContent = '🍃 LOW SPEC';
        hardwareBadge.title = 'Low Spec Mode Enabled (Memory Pressure Limit & Process Throttling)';
      } else {
        specModeText.textContent = '⚡ HIGH SPEC';
        hardwareBadge.title = 'High Spec Mode Enabled (GPU Acceleration & Max Performance)';
      }
    }
  } catch (e) {
    console.error('Error fetching system specs:', e);
  }
}

// URL Normalizer
function normalizeUrl(input) {
  let url = input.trim();
  if (!url) return DEFAULT_HOMEPAGE;
  if (url.startsWith('about:') || url.startsWith('file://')) return url;
  if (url.includes('://')) return url;

  if (url.includes('.') && !url.includes(' ')) {
    return 'https://' + url;
  }
  return `https://www.google.com/search?q=${encodeURIComponent(url)}`;
}

// Tab Creation & Management
function createTab(url = DEFAULT_HOMEPAGE) {
  const tabId = 'tab-' + (++tabCounter);
  const targetUrl = normalizeUrl(url);

  // Tab Element
  const tabEl = document.createElement('div');
  tabEl.className = 'tab';
  tabEl.id = 'tab-btn-' + tabId;
  tabEl.innerHTML = `
    <span class="tab-title">New Tab</span>
    <button class="tab-close"><i class="fa-solid fa-xmark"></i></button>
  `;

  // Webview Element
  const webview = document.createElement('webview');
  webview.id = 'wv-' + tabId;
  webview.src = targetUrl;
  webview.setAttribute('allowpopups', 'true');

  const tabObj = {
    id: tabId,
    tabEl,
    webview,
    url: targetUrl,
    title: 'New Tab'
  };

  tabs.push(tabObj);

  // Click & Close events
  tabEl.addEventListener('click', (e) => {
    if (!e.target.classList.contains('tab-close') && !e.target.parentElement.classList.contains('tab-close')) {
      switchTab(tabId);
    }
  });

  tabEl.querySelector('.tab-close').addEventListener('click', (e) => {
    e.stopPropagation();
    closeTab(tabId);
  });

  // Webview Events
  webview.addEventListener('did-start-loading', () => {
    statusText.textContent = `Loading: ${webview.getURL() || targetUrl}...`;
  });

  webview.addEventListener('did-finish-load', () => {
    statusText.textContent = 'Page Loaded | Bharat Browser 🇮🇳';
    updateNavButtons();

    // Inject DarkReader CSS if active
    if (darkReaderActive) {
      applyDarkReaderToWebview(webview);
    }
  });

  webview.addEventListener('page-title-updated', (e) => {
    tabObj.title = e.title;
    tabEl.querySelector('.tab-title').textContent = e.title;
  });

  webview.addEventListener('did-navigate', (e) => {
    tabObj.url = e.url;
    if (activeTabId === tabId) {
      urlInput.value = e.url;
      updateNavButtons();
    }
  });

  webview.addEventListener('new-window', (e) => {
    e.preventDefault();
    createTab(e.url);
  });

  webview.addEventListener('did-navigate-in-page', (e) => {
    tabObj.url = e.url;
    if (activeTabId === tabId) {
      urlInput.value = e.url;
      updateNavButtons();
    }
  });

  tabsContainer.appendChild(tabEl);
  webviewsContainer.appendChild(webview);

  switchTab(tabId);
}

function switchTab(tabId) {
  activeTabId = tabId;
  tabs.forEach(t => {
    if (t.id === tabId) {
      t.tabEl.classList.add('active');
      t.webview.classList.add('active');
      urlInput.value = t.url || DEFAULT_HOMEPAGE;
      updateNavButtons();
    } else {
      t.tabEl.classList.remove('active');
      t.webview.classList.remove('active');
    }
  });
}

function closeTab(tabId) {
  if (tabs.length === 1) {
    createTab(DEFAULT_HOMEPAGE);
  }

  const index = tabs.findIndex(t => t.id === tabId);
  if (index !== -1) {
    const tabObj = tabs[index];
    tabObj.tabEl.remove();
    tabObj.webview.remove();
    tabs.splice(index, 1);

    if (activeTabId === tabId) {
      const nextTab = tabs[Math.max(0, index - 1)];
      if (nextTab) {
        switchTab(nextTab.id);
      }
    }
  }
}

function updateNavButtons() {
  const currentTab = tabs.find(t => t.id === activeTabId);
  if (currentTab && currentTab.webview) {
    btnBack.disabled = !currentTab.webview.canGoBack();
    btnForward.disabled = !currentTab.webview.canGoForward();
  }
}

// Navigation Handlers
btnBack.addEventListener('click', () => {
  const currentTab = tabs.find(t => t.id === activeTabId);
  if (currentTab && currentTab.webview.canGoBack()) currentTab.webview.goBack();
});

btnForward.addEventListener('click', () => {
  const currentTab = tabs.find(t => t.id === activeTabId);
  if (currentTab && currentTab.webview.canGoForward()) currentTab.webview.goForward();
});

btnReload.addEventListener('click', () => {
  const currentTab = tabs.find(t => t.id === activeTabId);
  if (currentTab) currentTab.webview.reload();
});

btnHome.addEventListener('click', () => {
  const currentTab = tabs.find(t => t.id === activeTabId);
  if (currentTab) currentTab.webview.loadURL(DEFAULT_HOMEPAGE);
});

// Screenshot Handler (Saves JPEG automatically to Desktop)
const btnScreenshot = document.getElementById('btn-screenshot');
if (btnScreenshot) {
  btnScreenshot.addEventListener('click', async () => {
    const currentTab = tabs.find(t => t.id === activeTabId);
    if (!currentTab || !currentTab.webview) {
      statusText.textContent = '⚠️ No active webpage tab to capture screenshot.';
      return;
    }

    try {
      statusText.textContent = '📸 Capturing webpage screenshot...';
      const nativeImage = await currentTab.webview.capturePage();
      const dataUrl = nativeImage.toJPEG(90);

      if (window.bharatAPI) {
        const res = await window.bharatAPI.saveScreenshot(dataUrl);
        if (res && res.success) {
          statusText.textContent = `📸 Screenshot saved to Desktop: ${res.filename}`;
        } else {
          statusText.textContent = '❌ Failed to save screenshot to Desktop.';
        }
      }
    } catch (e) {
      console.error('Error capturing screenshot:', e);
      statusText.textContent = '❌ Screenshot capture failed.';
    }
  });
}

urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const currentTab = tabs.find(t => t.id === activeTabId);
    if (currentTab) {
      const target = normalizeUrl(urlInput.value);
      currentTab.webview.loadURL(target);
    }
  }
});

btnNewTab.addEventListener('click', () => createTab());

// DarkReader Engine Checkbox in Settings Modal
if (chkDarkReader) {
  chkDarkReader.addEventListener('change', async (e) => {
    darkReaderActive = e.target.checked;
    const currentTab = tabs.find(t => t.id === activeTabId);
    if (currentTab && currentTab.webview) {
      if (darkReaderActive) {
        await applyDarkReaderToWebview(currentTab.webview);
      } else {
        currentTab.webview.reload();
      }
    }
  });
}

async function applyDarkReaderToWebview(webview) {
  try {
    if (window.bharatAPI) {
      const css = await window.bharatAPI.getDarkReaderCSS();
      webview.insertCSS(css);
    }
  } catch (e) {
    console.error('Error inserting DarkReader CSS:', e);
  }
}

// Integrated Download Manager UI Updates
if (window.bharatAPI) {
  window.bharatAPI.onTrackerBlocked((data) => {
    shieldCountEl.textContent = data.count;
  });

  window.bharatAPI.onDownloadUpdate((downloads) => {
    downloadCountEl.textContent = downloads.length;
    renderDownloadList(downloads);
  });
}

function renderDownloadList(downloads) {
  if (!downloads || downloads.length === 0) {
    downloadList.innerHTML = '<div class="empty-downloads">No active or recent downloads</div>';
    return;
  }

  downloadList.innerHTML = downloads.map(item => {
    const percent = Math.round(item.progress * 100);
    return `
      <div class="download-card">
        <div class="download-filename">${item.filename}</div>
        <div class="download-progress-bar">
          <div class="download-progress-fill" style="width: ${percent}%;"></div>
        </div>
        <div class="download-status-text">
          <span>${item.state.toUpperCase()}</span>
          <span>${percent}%</span>
        </div>
      </div>
    `;
  }).join('');
}

if (btnOpenDownloads) {
  btnOpenDownloads.addEventListener('click', () => {
    settingsModal.classList.add('hidden');
    downloadDrawer.classList.remove('hidden');
  });
}
closeDrawer.addEventListener('click', () => downloadDrawer.classList.add('hidden'));

// One-Click Clear History & Cookies Handler
const btnClearData = document.getElementById('btn-clear-data');
const clearStatusToast = document.getElementById('clear-status-toast');

if (btnClearData) {
  btnClearData.addEventListener('click', async () => {
    try {
      if (window.bharatAPI) {
        const res = await window.bharatAPI.clearBrowsingData();
        if (res && res.success) {
          clearStatusToast.classList.remove('hidden');
          setTimeout(() => {
            clearStatusToast.classList.add('hidden');
          }, 3500);
        }
      }
    } catch (e) {
      console.error('Error clearing storage data:', e);
    }
  });
}

// Settings Modal
btnSettings.addEventListener('click', () => settingsModal.classList.remove('hidden'));
closeModal.addEventListener('click', () => settingsModal.classList.add('hidden'));

// Global Keyboard Shortcuts (Ctrl+T, Ctrl+W, Ctrl+R, Ctrl+L, F5)
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey) {
    const key = e.key.toLowerCase();
    if (key === 't') {
      e.preventDefault();
      createTab(DEFAULT_HOMEPAGE);
    } else if (key === 'w') {
      e.preventDefault();
      if (activeTabId) closeTab(activeTabId);
    } else if (key === 'r') {
      e.preventDefault();
      const currentTab = tabs.find(t => t.id === activeTabId);
      if (currentTab) currentTab.webview.reload();
    } else if (key === 'l') {
      e.preventDefault();
      urlInput.focus();
      urlInput.select();
    }
  } else if (e.key === 'F5') {
    e.preventDefault();
    const currentTab = tabs.find(t => t.id === activeTabId);
    if (currentTab) currentTab.webview.reload();
  }
});

// Initialize App
initSystemSpecs();
createTab(DEFAULT_HOMEPAGE);
