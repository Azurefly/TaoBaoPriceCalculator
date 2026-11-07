"""Private loopback service for the packaged desktop app; never a public server."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time

VERSION = '0.3.2'


def prepare_environment(data_dir: Path) -> Path:
    path = data_dir.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ['PROMOPATH_DATA_DIR'] = str(path)
    os.environ['PROMOPATH_NO_OPEN'] = '1'
    return path


def bind_loopback() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))  # Keep bound; no probe-close-bind race.
    sock.listen(128)
    sock.set_inheritable(False)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    data = prepare_environment(Path(args.data_dir))
    # Import only after choosing a writable per-user directory, including frozen mode.
    from app import __version__ as core_version
    from app import main as core
    import uvicorn

    if args.self_test:
        assert (core.ROOT / 'app/static/index.html').is_file()
        assert (core.ROOT / 'app/static/app.js').is_file()
        for name in ('demo', 'historical'):
            core.Project.model_validate_json((core.ROOT / f'examples/{name}.json').read_text('utf-8'))
        core.store.snapshot()
        print(json.dumps({'self_test': 'passed', 'version': VERSION, 'core_version': core_version}), flush=True)
        return 0

    # Every launch has a random desktop secret. Do not accept unauthenticated startup.
    key = os.environ.pop('PROMOPATH_DESKTOP_KEY', '')
    if len(key) < 32:
        raise ValueError('Desktop authentication key is missing')
    core.TOKEN = key
    log_dir = data / 'logs'
    log_dir.mkdir(mode=0o700, exist_ok=True)
    handler = RotatingFileHandler(log_dir / 'service.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    sock = bind_loopback()
    server = uvicorn.Server(uvicorn.Config(core.app, log_config=None, access_log=False, loop='asyncio',
                                          http='h11', ws='none', lifespan='on', timeout_graceful_shutdown=10))

    @core.app.get('/api/desktop/info')
    async def info():
        return {'desktop_version': VERSION, 'core_version': core_version, 'local_only': True,
                'automatic_payment': False, 'data_dir': str(data)}

    @core.app.post('/api/desktop/shutdown')
    async def shutdown():
        # Existing token and same-origin middleware also protects this endpoint.
        # Do not interrupt an in-flight optimize/save request; uvicorn drains requests.
        asyncio.get_running_loop().call_later(0.1, setattr, server, 'should_exit', True)
        return {'stopping': True}

    def parent_watch():
        # Pipe is kept open by Electron. EOF means the parent has crashed/exited.
        try:
            while os.read(sys.stdin.fileno(), 4096):
                pass
        finally:
            server.should_exit = True

    def announce():
        for _ in range(1200):
            if server.started:
                print(json.dumps({'event': 'ready', 'port': sock.getsockname()[1], 'version': VERSION}), flush=True)
                return
            if server.should_exit:
                return
            time.sleep(0.05)
        server.should_exit = True

    threading.Thread(target=parent_watch, daemon=True).start()
    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception('Desktop backend startup failed')
        raise
