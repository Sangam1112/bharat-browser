const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('bharatAPI', {
  getSystemSpecs: () => ipcRenderer.invoke('get-system-specs'),
  getShieldStats: () => ipcRenderer.invoke('get-shield-stats'),
  getDarkReaderCSS: () => ipcRenderer.invoke('get-darkreader-css'),
  getDownloads: () => ipcRenderer.invoke('get-downloads'),
  clearBrowsingData: () => ipcRenderer.invoke('clear-browsing-data'),
  saveScreenshot: (dataUrl) => ipcRenderer.invoke('save-screenshot', dataUrl),
  onTrackerBlocked: (callback) => {
    ipcRenderer.on('tracker-blocked-event', (event, data) => callback(data));
  },
  onDownloadUpdate: (callback) => {
    ipcRenderer.on('download-manager-update', (event, data) => callback(data));
  }
});
