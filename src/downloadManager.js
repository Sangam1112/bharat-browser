// Integrated Download Manager Module
class DownloadManager {
  constructor() {
    this.downloads = [];
  }

  addDownload(item, webContents) {
    const downloadObj = {
      id: 'dl-' + Date.now() + '-' + Math.floor(Math.random() * 1000),
      filename: item.getFilename(),
      url: item.getURL(),
      totalBytes: item.getTotalBytes(),
      receivedBytes: 0,
      state: 'downloading',
      progress: 0,
      savePath: item.getSavePath()
    };

    this.downloads.push(downloadObj);

    item.on('updated', (event, state) => {
      if (state === 'interrupted') {
        downloadObj.state = 'interrupted';
      } else if (state === 'progressing') {
        if (item.isPaused()) {
          downloadObj.state = 'paused';
        } else {
          downloadObj.state = 'downloading';
          downloadObj.receivedBytes = item.getReceivedBytes();
          downloadObj.progress = item.getTotalBytes() > 0 ? (downloadObj.receivedBytes / item.getTotalBytes()) : 0;
        }
      }
      this.notify(webContents);
    });

    item.once('done', (event, state) => {
      if (state === 'completed') {
        downloadObj.state = 'completed';
        downloadObj.progress = 1.0;
      } else {
        downloadObj.state = 'failed';
      }
      this.notify(webContents);
    });

    this.notify(webContents);
  }

  notify(webContents) {
    if (webContents && !webContents.isDestroyed()) {
      webContents.send('download-manager-update', this.downloads);
    }
  }

  getDownloads() {
    return this.downloads;
  }
}

module.exports = new DownloadManager();
