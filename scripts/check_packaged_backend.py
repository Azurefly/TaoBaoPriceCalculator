"""Real HTTP and lifecycle smoke for the frozen Python distribution on every OS."""
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

root = Path(__file__).resolve().parents[1]
binary = root / 'dist/TaoBaoPriceCalculator-service' / ('TaoBaoPriceCalculator-service.exe' if os.name == 'nt' else 'TaoBaoPriceCalculator-service')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with tempfile.TemporaryDirectory(prefix='tbpc-packaged-') as temp:
    data = Path(temp) / '中文 data'
    result = subprocess.run([str(binary), '--data-dir', str(data), '--self-test'], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    key = secrets.token_hex(32)
    child = subprocess.Popen([str(binary), '--data-dir', str(data)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, 'PROMOPATH_DESKTOP_KEY': key})
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(child.stdout.readline()), daemon=True).start()
    def call(route, method='GET', body=None, authenticated=True):
        headers = {'X-Local-Token': key} if authenticated else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if method == 'PUT':
            headers['X-Project-Revision'] = call('/api/project')['revision']
        req = urllib.request.Request(url + route, headers=headers, method=method,
            data=json.dumps(body).encode() if body is not None else None)
        with opener.open(req, timeout=30) as response:
            raw = response.read().decode()
            return json.loads(raw) if route.startswith('/api/') else raw
    try:
        handshake = json.loads(lines.get(timeout=45))
        url = 'http://127.0.0.1:' + str(handshake['port'])
        assert handshake['version'] == '0.3.0'
        assert key in call('/')
        assert 'use strict' in call('/static/app.js')
        try:
            call('/api/project', authenticated=False)
            raise AssertionError('Unauthenticated request unexpectedly succeeded')
        except urllib.error.HTTPError as error:
            assert error.code == 403
        demo = call('/api/examples/demo')
        call('/api/project', 'PUT', demo)
        assert call('/api/simulate', 'POST', {'budget': 100})['quote_count'] > 0
        answer = call('/api/optimize', 'POST', {'mode': 'model', 'step_seconds': 0})
        assert answer['cash_due_cents'] == 120200, answer
        call('/api/desktop/shutdown', 'POST')
        child.stdin.close()
        assert child.wait(timeout=20) == 0
        print(json.dumps({'packaged_backend': 'passed', 'cash_due_cents': answer['cash_due_cents'], 'platform': sys.platform}))
    finally:
        if child.poll() is None:
            child.stdin.close()
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        if child.returncode:
            print(child.stderr.read(), file=sys.stderr)
