"""Verify the loopback HTTP service; try browser navigation without bypassing policy."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
out=Path(os.getenv('PROMOPATH_TEST_ARTIFACTS',str(ROOT/'test-artifacts')))
out.mkdir(parents=True,exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env={**os.environ,'PROMOPATH_DATA_DIR':tempfile.mkdtemp(prefix='promopath-http-')}
server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],
                        cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
report={'taobao_accessed':False}
try:
    url=f'http://127.0.0.1:{port}'
    for _ in range(60):
        try:
            response=httpx.get(url,timeout=1)
            if response.status_code==200:break
        except httpx.HTTPError:pass
        time.sleep(.1)
    else:raise RuntimeError('本地HTTP服务未能启动')
    report['http_status']=response.status_code
    with sync_playwright() as pw:
        exe=os.getenv('PROMOPATH_TEST_CHROMIUM')
        browser=pw.chromium.launch(headless=True,**({'executable_path':exe} if exe else {}),args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.goto(url,wait_until='networkidle',timeout=10000)
            page.locator('#stats .stat').nth(3).wait_for(timeout=5000)
            report['browser_http_navigation']='passed'
            report['page_errors']=errors
        except Exception as exc:
            report['browser_http_navigation']='not_verified'
            report['reason']=str(exc)[:1200]
        finally:browser.close()
finally:
    server.terminate()
    try:server.wait(timeout=5)
    except subprocess.TimeoutExpired:server.kill();server.wait()
(out/'local-http.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
assert report.get('http_status')==200
