'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {channel} = require('./release-config.cjs');
const {run, submit} = require('./notary-tools.cjs');
module.exports = async context => {
  if (context.electronPlatformName !== 'darwin' || channel() !== 'distribution') return;
  const app = path.join(context.appOutDir, context.packager.appInfo.productFilename + '.app');
  run('/usr/bin/codesign', ['--verify', '--deep', '--strict', app]);
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'tbpc-notary-'));
  try {
    const zip = path.join(temp, 'application.zip');
    run('/usr/bin/ditto', ['-c', '-k', '--keepParent', app, zip]);
    submit(zip, app);
    run('/usr/bin/codesign', ['--verify', '--deep', '--strict', app]);
  } finally { fs.rmSync(temp, {recursive: true, force: true}); }
};
