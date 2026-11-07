'use strict';
// Sign nested code before the outer app is sealed. No --deep signing or library entitlements.
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {channel, buildConfig} = require('./release-config.cjs');
const MACHO = new Set(['feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca']);
function isMachO(file) {
  const fd = fs.openSync(file, 'r');
  try { const b = Buffer.alloc(4); return fs.readSync(fd, b, 0, 4, 0) === 4 && MACHO.has(b.toString('hex')); }
  finally { fs.closeSync(fd); }
}
function collect(directory) {
  const native = [], bundles = [];
  for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
    const file = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      const found = collect(file); native.push(...found.native); bundles.push(...found.bundles);
      if (/\.(framework|app|xpc|bundle)$/.test(entry.name)) bundles.push(file);
    } else if (entry.isFile() && isMachO(file)) native.push(file);
  }
  return {native, bundles};
}
function signArgs(file, executable, env = process.env) {
  const official = channel(env) === 'distribution';
  if (official) buildConfig(env, 'darwin');
  const args = ['--force', '--sign', official ? env.TBPC_MAC_CERT_SHA1 : '-',
    official ? '--timestamp' : '--timestamp=none'];
  if (official) args.push('--keychain', env.CSC_KEYCHAIN);
  if (executable) args.push('--options', 'runtime', '--entitlements', path.join(__dirname,
    official ? 'entitlements.distribution.plist' : 'entitlements.mac.plist'), '--generate-entitlement-der');
  return [...args, file];
}
async function signBackend(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const config = buildConfig(process.env, 'darwin');
  if (context.packager.platformSpecificBuildOptions.identity !== config.mac.identity)
    throw new Error('Expected explicit ad-hoc or validated Developer ID identity; refusing signing opt-out');
  const app = path.join(context.appOutDir, context.packager.appInfo.productFilename + '.app');
  const {native, bundles} = collect(path.join(app, 'Contents', 'Resources', 'backend'));
  if (!native.length) throw new Error('Frozen backend contains no native binaries');
  for (const file of native) {
    const info = execFileSync('/usr/bin/file', ['-b', file], {encoding: 'utf8'});
    execFileSync('/usr/bin/codesign', signArgs(file, /\bexecutable\b/.test(info)), {stdio: 'pipe'});
  }
  for (const bundle of bundles.sort((a, b) => b.split(path.sep).length - a.split(path.sep).length)) {
    execFileSync('/usr/bin/codesign', signArgs(bundle, /\.(app|xpc)$/.test(bundle)), {stdio: 'pipe'});
  }
  for (const file of native) execFileSync('/usr/bin/codesign', ['--verify', '--strict', file], {stdio: 'pipe'});
  console.log(`Signed and verified ${native.length} embedded native files (${channel()}).`);
}
module.exports = signBackend;
Object.assign(module.exports, {isMachO, collect, signArgs});
