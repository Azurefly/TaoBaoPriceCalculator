"""Run on loopback only. Do not expose the local browser profile via a public server."""
import os
import sys
import webbrowser
import threading
if sys.version_info < (3, 11):
    raise SystemExit('需要Python 3.11或更高版本')
import uvicorn

if __name__ == '__main__':
    port = int(os.environ.get('PROMOPATH_PORT', '8765'))
    if not 1024 <= port <= 65535:
        raise SystemExit('端口必须为1024~65535')
    url = f'http://127.0.0.1:{port}'
    print(f'PromoPath 已准备启动：{url}\n仅本地访问；不自动下单/付款。Ctrl+C停止。')
    if os.environ.get('PROMOPATH_NO_OPEN') != '1':
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, access_log=False)
