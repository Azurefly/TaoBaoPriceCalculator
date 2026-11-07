"""Real HTTP subprocess tests; no Electron/packager dependency required locally."""
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import pytest

ROOT = Path(__file__).resolve().parents[1]


def request(url, token=None, method='GET', origin=None):
    headers = {'X-Local-Token': token} if token else {}
    if origin:
        headers['Origin'] = origin
    req = urllib.request.Request(url, headers=headers, method=method)
    # Never route loopback through an enterprise/environment proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
        return r.status, r.read().decode()


@pytest.fixture
def service(tmp_path):
    token = secrets.token_hex(32)
    env = {**os.environ, 'PROMOPATH_DESKTOP_KEY': token, 'PYTHONUNBUFFERED': '1'}
    proc = subprocess.Popen([sys.executable, str(ROOT / 'desktop/entry.py'), '--data-dir', str(tmp_path / '中文数据 with spaces')],
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
    try:
        line = lines.get(timeout=20)
        assert line, proc.stderr.read() if proc.poll() is not None else 'No startup line'
        info = json.loads(line)
        assert info['event'] == 'ready'
        yield proc, 'http://127.0.0.1:' + str(info['port']), token
    finally:
        if proc.poll() is None:
            proc.stdin.close()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


def test_desktop_health_and_rendered_html(service):
    proc, url, token = service
    status, html = request(url)
    assert status == 200 and token in html and 'PromoPath' in html
    info = json.loads(request(url + '/api/desktop/info', token)[1])
    assert info['desktop_version'] == '0.3.0'
    assert info['local_only'] is True and info['automatic_payment'] is False
    assert '中文数据 with spaces' in info['data_dir']
    assert request(url + '/static/app.js')[0] == 200


def test_desktop_rejects_missing_token_and_cross_origin(service):
    _, url, token = service
    for path, supplied, origin in [('/api/project', None, None), ('/api/desktop/shutdown', None, None),
                                   ('/api/desktop/shutdown', token, 'https://evil.example')]:
        with pytest.raises(urllib.error.HTTPError) as error:
            request(url + path, supplied, 'POST' if 'shutdown' in path else 'GET', origin)
        assert error.value.code == 403
    assert request(url + '/api/project', token)[0] == 200


def test_desktop_authenticated_shutdown(service):
    proc, url, token = service
    assert json.loads(request(url + '/api/desktop/shutdown', token, 'POST')[1])['stopping']
    assert proc.wait(timeout=15) == 0


def test_desktop_parent_eof_stops_child(service):
    proc, _, _ = service
    proc.stdin.close()
    assert proc.wait(timeout=15) == 0


def test_desktop_self_test_supports_read_only_install_cwd(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'desktop/entry.py'), '--data-dir', str(tmp_path / 'profile'), '--self-test'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['self_test'] == 'passed'
    assert not (tmp_path / '.data').exists()


def test_desktop_refuses_missing_parent_key(tmp_path):
    env = dict(os.environ); env.pop('PROMOPATH_DESKTOP_KEY', None)
    result = subprocess.run([sys.executable, str(ROOT / 'desktop/entry.py'), '--data-dir', str(tmp_path)],
                            env=env, input='', capture_output=True, text=True, timeout=20)
    assert result.returncode != 0


def test_desktop_uses_bound_ephemeral_loopback_socket():
    from desktop.service import bind_loopback
    with bind_loopback() as first, bind_loopback() as second:
        assert first.getsockname()[0] == '127.0.0.1'
        assert first.getsockname()[1] != second.getsockname()[1]
        with socket.socket() as other:
            with pytest.raises(OSError):
                other.bind(first.getsockname())


def test_desktop_version_matches_package():
    from desktop.service import VERSION
    assert json.loads((ROOT / 'desktop/package.json').read_text())['version'] == VERSION
