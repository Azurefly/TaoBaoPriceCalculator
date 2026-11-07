'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { isLocalURL, safeExternal, parseReady } = require('./security.cjs');
const p = require('./package.json');
test('local origin requires exact scheme, host and port', () => {
  const o = 'http://127.0.0.1:43210';
  assert.ok(isLocalURL(o + '/api/project', o));
  for (const bad of ['http://localhost:43210', 'https://127.0.0.1:43210',
    'http://127.0.0.1:43211', 'http://127.0.0.1.evil:43210', 'http://user@127.0.0.1:43210', 'javascript:alert(1)'])
    assert.equal(isLocalURL(bad, o), false);
});
test('external links restricted to owned repository', () => {
  assert.ok(safeExternal('https://github.com/Azurefly/TaoBaoPriceCalculator/releases'));
  for (const bad of ['file:///etc/passwd','https://github.com.evil/Azurefly/TaoBaoPriceCalculator',
    'https://github.com/Azurefly/TaoBaoPriceCalculator-evil', 'https://github.com/another/repo',
    'https://name@github.com/Azurefly/TaoBaoPriceCalculator', 'https://github.com:8443/Azurefly/TaoBaoPriceCalculator'])
    assert.equal(safeExternal(bad), false);
});
test('readiness requires port and matching packaged version', () => {
  assert.equal(parseReady('some log line', p.version), null);
  assert.equal(parseReady(JSON.stringify({ event: 'ready', port: 54321, version: p.version }), p.version), 'http://127.0.0.1:54321');
  for (const port of [0, -1, 80, 65536, '54321', 54321.5])
    assert.throws(() => parseReady(JSON.stringify({ event: 'ready', port, version: p.version }), p.version));
  assert.throws(() => parseReady(JSON.stringify({ event: 'ready', port: 54321, version: 'old' }), p.version));
});
test('installer does not remove user data or bundle source data directories', () => {
  assert.equal(p.build.nsis.deleteAppDataOnUninstall, false);
  assert.equal(p.build.asar, true);
  assert.deepEqual(p.build.files, ['main.cjs', 'security.cjs', 'package.json']);
  assert.deepEqual(p.build.extraResources, [{ from: '../dist/TaoBaoPriceCalculator-service', to: 'backend' }]);
});
