'use strict';
const {channel, buildConfig} = require('./release-config.cjs');
const {run, submit} = require('./notary-tools.cjs');
module.exports = async result => {
  if (process.platform !== 'darwin' || channel() !== 'distribution') return [];
  buildConfig(process.env, 'darwin');
  const files = result.artifactPaths.filter(p => p.endsWith('.dmg'));
  if (!files.length) throw new Error('Distribution build produced no DMG');
  for (const file of files) {
    run('/usr/bin/codesign', ['--force', '--sign', process.env.TBPC_MAC_CERT_SHA1,
      '--keychain', process.env.CSC_KEYCHAIN, '--timestamp', file]);
    submit(file);
  }
  return [];
};
