'use strict';
const RELEASES = 'https://github.com/Azurefly/TaoBaoPriceCalculator/releases';
function isLocalURL(value, origin) {
  try { const u = new URL(value); return u.origin === origin && !u.username && !u.password; }
  catch { return false; }
}
function safeExternal(value) {
  try {
    const u = new URL(value);
    return u.protocol === 'https:' && !u.username && !u.password &&
      u.hostname === 'github.com' && u.port === '' &&
      (u.pathname === '/Azurefly/TaoBaoPriceCalculator' ||
       u.pathname.startsWith('/Azurefly/TaoBaoPriceCalculator/'));
  } catch { return false; }
}
function parseReady(line, version) {
  let msg; try { msg = JSON.parse(line); } catch { return null; }
  if (msg.event !== 'ready') return null;
  if (!Number.isInteger(msg.port) || msg.port < 1024 || msg.port > 65535 || msg.version !== version)
    throw new Error('Invalid backend handshake');
  return `http://127.0.0.1:${msg.port}`;
}
module.exports = { RELEASES, isLocalURL, safeExternal, parseReady };
