'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const hook = require('./sign-backend.cjs');
const config = require('./package.json').build;
test('Mac preview explicitly signs and keeps hardened runtime enabled', () => {
  assert.equal(config.mac.identity, '-');
  assert.equal(config.mac.hardenedRuntime, true);
  assert.equal(config.mac.notarize, false);
  assert.equal(config.afterPack, './sign-backend.cjs');
});
test('Ad-hoc entitlements grant JIT and library loading, not debugging or devices', () => {
  const text = fs.readFileSync(path.join(__dirname, config.mac.entitlements), 'utf8');
  assert.match(text, /com.apple.security.cs.allow-jit/);
  assert.match(text, /com.apple.security.cs.disable-library-validation/);
  assert.doesNotMatch(text, /get-task-allow|allow-dyld|device.camera|device.microphone/);
});
test('Native-file detection ignores data and handles tiny files', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tbpc-sign-test-'));
  try {
    fs.writeFileSync(path.join(dir, 'native'), Buffer.from('cffaedfe00000000', 'hex'));
    fs.writeFileSync(path.join(dir, 'empty'), '');
    fs.writeFileSync(path.join(dir, 'data'), 'plain text');
    assert.equal(hook.isMachO(path.join(dir, 'native')), true);
    assert.equal(hook.isMachO(path.join(dir, 'empty')), false);
    assert.equal(hook.collect(dir).native.length, 1);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
test('Signing hook does not run on Windows or Linux', async () => {
  await hook({ electronPlatformName: 'win32' });
  await hook({ electronPlatformName: 'linux' });
});
test('Signing refuses implicit opt-out rather than publishing unsigned output', async () => {
  await assert.rejects(hook({ electronPlatformName: 'darwin', appOutDir: os.tmpdir(),
    packager: { appInfo: { productFilename: 'TaoBaoPriceCalculator' }, platformSpecificBuildOptions: { identity: null } } }), /explicit ad-hoc/);
});
test('Installer does not auto-launch or delete user data on uninstall', () => {
  assert.equal(config.nsis.runAfterFinish, false);
  assert.equal(config.nsis.deleteAppDataOnUninstall, false);
});
