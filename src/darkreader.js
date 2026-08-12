// DarkReader Web View Extension Injector
const DARKREADER_CSS = `
html, body {
  background-color: #121212 !important;
  color: #e0e0e0 !important;
}
p, h1, h2, h3, h4, h5, h6, span, td, th, li {
  color: #e0e0e0 !important;
}
a {
  color: #82aaff !important;
}
input, textarea, select, button {
  background-color: #1e1e1e !important;
  color: #ffffff !important;
  border-color: #333333 !important;
}
img, video {
  filter: brightness(0.95) contrast(1.05) !important;
}
`;

function getDarkReaderCSS() {
  return DARKREADER_CSS;
}

module.exports = { getDarkReaderCSS };
