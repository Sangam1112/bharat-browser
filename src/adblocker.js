// 2-Stage Filter Ad & Tracker Blocker Engine (uBlock Origin Lite & Privacy Badger)
const KNOWN_AD_DOMAINS = new Set([
  'doubleclick.net', 'google-analytics.com', 'googlesyndication.com',
  'adservice.google.com', 'adnxs.com', 'scorecardresearch.com',
  'quantserve.com', 'outbrain.com', 'taboola.com', 'moatads.com',
  'amazon-adsystem.com', 'criteo.com', 'pubmatic.com', 'rubiconproject.com',
  'hotjar.com', 'crazyegg.com', 'fullstory.com', 'mixpanel.com',
  'segment.io', 'clarity.ms', 'mouseflow.com', 'luckyorange.com',
  'logrocket.com', 'sentry.io', 'bugsnag.com', 'pixel.facebook.com',
  'an.facebook.com', 'connect.facebook.net', 'ad.doubleclick.net'
]);

const UBOL_PATTERNS = [
  /\/ad\/server/i, /\/ads\/pixel/i, /adserver/i, /adsystem/i,
  /banner-ad/i, /popunder/i, /popup-ad/i, /telemetry/i
];

function isAdOrTracker(urlString) {
  try {
    const parsed = new URL(urlString);
    const hostname = parsed.hostname.toLowerCase();

    // Stage 1: O(1) Set domain pre-lookup
    const parts = hostname.split('.');
    for (let i = 0; i < parts.length - 1; i++) {
      const subdomain = parts.slice(i).join('.');
      if (KNOWN_AD_DOMAINS.has(subdomain)) {
        return true;
      }
    }

    // Stage 2: Path regex matching
    const pathAndQuery = parsed.pathname + parsed.search;
    for (let i = 0; i < UBOL_PATTERNS.length; i++) {
      if (UBOL_PATTERNS[i].test(pathAndQuery)) {
        return true;
      }
    }
  } catch (e) {
    // Invalid URL format
  }
  return false;
}

module.exports = { isAdOrTracker };
