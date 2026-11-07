'use strict';
// The default remains an explicitly ad-hoc preview. Production never falls back.
function channel(env = process.env) {
  const value = env.TBPC_RELEASE_CHANNEL || 'preview';
  if (!['preview', 'distribution'].includes(value)) throw new Error('Invalid TBPC_RELEASE_CHANNEL');
  return value;
}
function requireValues(env, names) {
  const missing = names.filter(name => !env[name] || !env[name].trim());
  if (missing.length) throw new Error('Missing release credentials: ' + missing.join(', '));
}
function buildConfig(env = process.env, platform = process.platform) {
  const config = JSON.parse(JSON.stringify(require('./package.json').build));
  if (channel(env) === 'preview') return config;
  if (platform === 'darwin') {
    requireValues(env, ['TBPC_MAC_IDENTITY', 'TBPC_MAC_CERT_SHA1', 'TBPC_TEAM_ID', 'CSC_KEYCHAIN', 'TBPC_NOTARY_PROFILE']);
    if (!/^Developer ID Application: .+ \([A-Z0-9]{10}\)$/.test(env.TBPC_MAC_IDENTITY) ||
        !env.TBPC_MAC_IDENTITY.endsWith('(' + env.TBPC_TEAM_ID + ')') ||
        !/^[A-Fa-f0-9]{40}$/.test(env.TBPC_MAC_CERT_SHA1)) throw new Error('Invalid Developer ID identity or team');
    config.forceCodeSigning = true;
    // electron-builder v26 finds the certificate type itself. The hook uses the exact hash.
    config.mac.identity = env.TBPC_MAC_IDENTITY.replace(/^Developer ID Application:\s*/, '');
    config.mac.entitlements = config.mac.entitlementsInherit = 'entitlements.distribution.plist';
    config.afterSign = './notarize.cjs';
    config.afterAllArtifactBuild = './notarize-dmg.cjs';
    config.mac.notarize = false; // Handled by fail-closed hooks, not silently skipped.
  } else if (platform === 'win32') {
    requireValues(env, ['WIN_CSC_LINK', 'WIN_CSC_KEY_PASSWORD']);
    config.forceCodeSigning = true;
    config.win.signAndEditExecutable = true;
    config.win.signExts = ['.exe', '.dll', '.pyd'];
  }
  return config;
}
module.exports = {channel, requireValues, buildConfig};
