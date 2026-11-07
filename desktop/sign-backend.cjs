'use strict';
// Sign the frozen Python/Playwright native code before electron-builder seals the app.
// Never use `codesign --deep` for signing: nested code is signed inside out.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const MACHO = new Set(['feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca']);
function isMachO(file) {
  const fd = fs.openSync(file, 'r');
  try { const b = Buffer.alloc(4); return fs.readSync(fd, b, 0, 4, 0) === 4 && MACHO.has(b.toString('hex')); }
  finally { fs.closeSync(fd); }
}
function collect(directory) {
  const native = [], bundles = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      const found = collect(file); native.push(...found.native); bundles.push(...found.bundles);
      if (/\.(framework|app|xpc|bundle)$/.test(entry.name)) bundles.push(file);
    } else if (entry.isFile() && isMachO(file)) native.push(file);
  }
  return { native, bundles };
}
async function signBackend(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const app = path.join(context.appOutDir, context.packager.appInfo.productFilename + '.app');
  const backend = path.join(app, 'Contents', 'Resources', 'backend');
  const identity = context.packager.platformSpecificBuildOptions.identity;
  if (identity !== '-') throw new Error('This preview hook requires explicit ad-hoc identity; do not silently skip signing.');
  const entitlements = path.join(__dirname, 'entitlements.mac.plist');
  const { native, bundles } = collect(backend);
  if (!native.length) throw new Error('Frozen backend contains no native binaries');
  for (const file of [...native, ...bundles]) {
    execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', '--timestamp=none', '--options', 'runtime',
      '--entitlements', entitlements, '--generate-entitlement-der', file], { stdio: 'pipe' });
  }
  for (const file of native) execFileSync('/usr/bin/codesign', ['--verify', '--strict', file], { stdio: 'pipe' });
  console.log(`Ad-hoc signed and verified ${native.length} embedded native binaries; publisher identity is NOT verified.`);
}
module.exports = signBackend;
module.exports.isMachO = isMachO;
module.exports.collect = collect;
