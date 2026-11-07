'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {channel, buildConfig} = require('./release-config.cjs');
const {signArgs} = require('./sign-backend.cjs');
const formal = {TBPC_RELEASE_CHANNEL: 'distribution', TBPC_MAC_IDENTITY: 'Developer ID Application: Fixture (TEAM123456)',
  TBPC_TEAM_ID: 'TEAM123456', TBPC_MAC_CERT_SHA1: 'a'.repeat(40), CSC_KEYCHAIN: '/tmp/test.keychain', TBPC_NOTARY_PROFILE: 'fixture'};
test('unknown channel fails instead of becoming a preview', () => assert.throws(() => channel({TBPC_RELEASE_CHANNEL: 'release-typo'})));
test('default channel is explicitly preview', () => {
  assert.equal(channel({}), 'preview');
  const c = buildConfig({}, 'darwin');
  assert.equal(c.mac.identity, '-'); assert.equal(c.mac.notarize, false);
});
for (const key of ['TBPC_MAC_IDENTITY', 'TBPC_TEAM_ID', 'TBPC_MAC_CERT_SHA1', 'CSC_KEYCHAIN', 'TBPC_NOTARY_PROFILE']) {
  test('distribution refuses missing ' + key, () => {
    const env = {...formal}; delete env[key];
    assert.throws(() => buildConfig(env, 'darwin'));
  });
}
test('distribution requires the expected Apple team', () => assert.throws(() => buildConfig({...formal, TBPC_TEAM_ID: 'OTHER12345'}, 'darwin')));
test('ad-hoc cannot masquerade as a distribution identity', () => assert.throws(() => buildConfig({...formal, TBPC_MAC_IDENTITY: '-'}, 'darwin')));
test('distribution signs app and final DMG through explicit notarization hooks', () => {
  const c = buildConfig(formal, 'darwin');
  assert.equal(c.forceCodeSigning, true);
  assert.equal(c.mac.identity, 'Fixture (TEAM123456)');
  assert.equal(c.afterSign, './notarize.cjs'); assert.equal(c.afterAllArtifactBuild, './notarize-dmg.cjs');
  assert.equal(c.mac.hardenedRuntime, true);
});
test('production entitlement set does not disable library validation', () => {
  const text = fs.readFileSync(path.join(__dirname, 'entitlements.distribution.plist'), 'utf8');
  assert.match(text, /allow-jit/); assert.doesNotMatch(text, /disable-library-validation|get-task-allow|allow-dyld/);
});
test('native libraries get no executable entitlements or runtime flag', () => {
  for (const env of [{}, formal]) {
    const args = signArgs('/tmp/native.dylib', false, env);
    assert.ok(!args.includes('--entitlements')); assert.ok(!args.includes('runtime')); assert.ok(!args.includes('--deep'));
  }
});
test('executable signing uses exact identity and timestamp in distribution mode', () => {
  const args = signArgs('/tmp/backend', true, formal);
  assert.ok(args.includes(formal.TBPC_MAC_CERT_SHA1)); assert.ok(args.includes('--timestamp'));
  assert.ok(args.includes('--keychain')); assert.ok(args.includes('--entitlements')); assert.ok(!args.includes('--deep'));
});
test('preview signs without network timestamping', () => assert.ok(signArgs('/tmp/backend', true, {}).includes('--timestamp=none')));
test('Windows distribution never silently accepts missing signing credentials', () => {
  assert.throws(() => buildConfig({TBPC_RELEASE_CHANNEL: 'distribution'}, 'win32'));
  const c = buildConfig({TBPC_RELEASE_CHANNEL: 'distribution', WIN_CSC_LINK: 'fixture', WIN_CSC_KEY_PASSWORD: 'fixture'}, 'win32');
  assert.equal(c.forceCodeSigning, true); assert.equal(c.win.signAndEditExecutable, true);
});
test('loading production config never mutates preview package defaults', () => {
  buildConfig(formal, 'darwin'); assert.equal(buildConfig({}, 'darwin').mac.identity, '-');
});
