// ClearURLs Engine: Strips tracking parameters from web URLs
const TRACKING_REGEX = /[?&](utm_[^&=]+|fbclid|gclid|msclkid|ref_|mc_eid|yclid|_hsenc|_openstat|si|igshid)=[^&]*/gi;

function sanitizeUrl(urlString) {
  if (!urlString || urlString.startsWith('about:') || urlString.startsWith('file://')) {
    return urlString;
  }
  let cleaned = urlString.replace(TRACKING_REGEX, '');
  if (!cleaned.includes('?') && cleaned.includes('&')) {
    cleaned = cleaned.replace('&', '?');
  }
  return cleaned;
}

module.exports = { sanitizeUrl };
