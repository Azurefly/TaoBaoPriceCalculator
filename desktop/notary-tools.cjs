'use strict';
const {execFileSync} = require('node:child_process');
const {buildConfig} = require('./release-config.cjs');
function run(tool, args) {
  try { return execFileSync(tool, args, {encoding: 'utf8', timeout: 1800000, stdio: ['ignore', 'pipe', 'pipe']}); }
  catch (_) { throw new Error(`${tool} failed during release signing/notarization; publication stopped.`); }
}
function submit(file, stapleTarget = file) {
  buildConfig(process.env, 'darwin');
  const result = JSON.parse(run('/usr/bin/xcrun', ['notarytool', 'submit', file,
    '--keychain-profile', process.env.TBPC_NOTARY_PROFILE, '--keychain', process.env.CSC_KEYCHAIN,
    '--wait', '--timeout', '25m', '--output-format', 'json']));
  if (result.status !== 'Accepted') throw new Error('Apple notarization was not Accepted; submission: ' + result.id);
  run('/usr/bin/xcrun', ['stapler', 'staple', stapleTarget]);
  run('/usr/bin/xcrun', ['stapler', 'validate', stapleTarget]);
  return result.id;
}
module.exports = {run, submit};
