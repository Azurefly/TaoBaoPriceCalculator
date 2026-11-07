'use strict';
const { app, BrowserWindow, Menu, dialog, shell, session } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const readline = require('node:readline');
const { RELEASES, isLocalURL, safeExternal, parseReady } = require('./security.cjs');
const VERSION = require('./package.json').version;
app.setName('TaoBaoPriceCalculator');
// Stable across installers, architectures and application upgrades.
const profile = path.join(app.getPath('appData'), 'TaoBaoPriceCalculator');
fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
app.setPath('userData', profile);
let window, backend, origin, stopping = false, quitting = false;
const key = randomBytes(32).toString('hex');
const smoke = process.env.PROMOPATH_DESKTOP_SMOKE === '1';
const dataRoot = process.env.PROMOPATH_DESKTOP_TEST_DATA && smoke
  ? path.resolve(process.env.PROMOPATH_DESKTOP_TEST_DATA)
  : path.join(app.getPath('userData'), 'storage');
function log(message) {
  fs.mkdirSync(dataRoot, { recursive: true, mode: 0o700 });
  const target = path.join(dataRoot, 'desktop.log');
  try { if (fs.statSync(target).size > 1_000_000) fs.renameSync(target, target + '.1'); } catch {}
  fs.appendFileSync(target, `${new Date().toISOString()} ${String(message).replaceAll(key, '[redacted]')}\n`, { mode: 0o600 });
}
function apiRequest(route, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request(origin + route, { method, headers: { 'X-Local-Token': key }, timeout: 5000 }, r => {
      let body = ''; r.setEncoding('utf8'); r.on('data', x => { body += x; });
      r.on('end', () => r.statusCode === 200 ? resolve(body) : reject(new Error(`HTTP ${r.statusCode}`)));
    });
    req.on('timeout', () => req.destroy(new Error('Service timeout')));
    req.on('error', reject); req.end();
  });
}
async function external(url) { if (safeExternal(url)) await shell.openExternal(url); }
function startBackend() {
  return new Promise((resolve, reject) => {
    const executable = path.join(process.resourcesPath, 'backend',
      'TaoBaoPriceCalculator-service' + (process.platform === 'win32' ? '.exe' : ''));
    const command = app.isPackaged ? executable : (process.env.PROMOPATH_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'));
    const args = app.isPackaged ? [] : [path.join(__dirname, 'entry.py')];
    args.push('--data-dir', dataRoot);
    backend = spawn(command, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PROMOPATH_DESKTOP_KEY: key, PYTHONUNBUFFERED: '1',
        PROMOPATH_BROWSER_CHANNEL: process.env.PROMOPATH_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : 'chrome') } });
    const timer = setTimeout(() => reject(new Error('本地服务启动超时，请查看日志。')), 60000);
    const lines = readline.createInterface({ input: backend.stdout });
    lines.on('line', line => {
      try { const ready = parseReady(line, VERSION); if (ready) { origin = ready; clearTimeout(timer); resolve(); } }
      catch (e) { clearTimeout(timer); reject(e); }
    });
    backend.stdin.on('error', e => log(`Backend pipe: ${e.message}`));
    backend.stderr.on('data', data => log(data.toString()));
    backend.once('error', e => { clearTimeout(timer); reject(e); });
    backend.once('exit', (code, signal) => {
      clearTimeout(timer);
      if (!origin) reject(new Error(`本地服务提前退出 (${code ?? signal})`));
      else if (!stopping) {
        log(`Backend unexpectedly exited: ${code ?? signal}`);
        if (!smoke) dialog.showErrorBox('本地服务已停止', `请重新打开应用。数据保存在：${dataRoot}`);
        app.exit(1);
      }
    });
  });
}
async function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  stopping = true;
  const exited = new Promise(resolve => backend.once('exit', resolve));
  if (origin) await apiRequest('/api/desktop/shutdown', 'POST').catch(() => {});
  // EOF also stops the child if HTTP is unavailable.
  backend.stdin.end();
  let timer;
  await Promise.race([exited, new Promise(resolve => { timer = setTimeout(() => { backend.kill(); resolve(); }, 12000); })]);
  clearTimeout(timer);
}
function createWindow() {
  window = new BrowserWindow({ title: `淘宝优惠计算器 ${VERSION}`, width: 1240, height: 940,
    minWidth: 700, minHeight: 640, show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, spellcheck: false } });
  window.webContents.on('will-navigate', (event, url) => { if (!isLocalURL(url, origin)) event.preventDefault(); });
  window.webContents.setWindowOpenHandler(({ url }) => { external(url).catch(log); return { action: 'deny' }; });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.once('ready-to-show', () => { if (!smoke) window.show(); });
  window.webContents.on('did-fail-load', (_, code, description) => log(`Page load: ${code} ${description}`));
  return window.loadURL(origin);
}
function setMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: '应用', submenu: [
      { label: '打开数据目录', click: () => shell.openPath(dataRoot) },
      { label: '下载新版本', click: () => external(RELEASES).catch(log) },
      { label: '关于 / 安装说明', click: () => dialog.showMessageBox({ type: 'info', title: '淘宝优惠计算器',
        message: `TaoBaoPriceCalculator ${VERSION}`,
        detail: '本地计算，不自动下单、支付或退款。\n点击“一键体验”可立即查看示例，不改真实数据。\n旧清单：右上角“更多 → 导入以前的清单”。\n高级功能：右上角“更多 → 高级设置与旧版界面”。\n覆盖安装保留数据。签名与公证状态以所下载版本的发布说明为准。' }) },
      { type: 'separator' }, { role: 'quit', label: '退出' }] },
    { label: '编辑', submenu: [{ role: 'undo', label: '撤销' }, { role: 'redo', label: '重做' },
      { type: 'separator' }, { role: 'cut', label: '剪切' }, { role: 'copy', label: '复制' },
      { role: 'paste', label: '粘贴' }, { role: 'selectAll', label: '全选' }] },
    { label: '视图', submenu: [{ role: 'reload', label: '刷新页面' }, { role: 'resetZoom', label: '实际大小' },
      { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' }, { role: 'togglefullscreen', label: '全屏' }] }
  ]));
}
if (!app.requestSingleInstanceLock()) { app.quit(); }
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault(); quitting = true;
    stopBackend().finally(() => app.quit());
  });
  app.whenReady().then(async () => {
    fs.mkdirSync(dataRoot, { recursive: true, mode: 0o700 });
    session.defaultSession.setPermissionRequestHandler((_, __, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
      const allowed = origin && (isLocalURL(details.url, origin) || details.url.startsWith(`blob:${origin}/`));
      callback({ cancel: !allowed });
    });
    await startBackend();
    await apiRequest('/api/desktop/info');
    setMenu(); await createWindow();
    if (smoke) {
      const title = await window.webContents.executeJavaScript('document.title');
      const hasToken = await window.webContents.executeJavaScript('!!document.querySelector("meta[name=local-token]")');
      if (!title || !hasToken) throw new Error('Packaged renderer smoke failed');
      await window.webContents.executeJavaScript(`new Promise((resolve, reject) => {
        let attempts = 0;
        const timer = setInterval(() => {
          if (document.body.dataset.ready === 'true') {
            clearInterval(timer);
            if (typeof window.require !== 'undefined' || typeof window.process !== 'undefined')
              reject(new Error('Unexpected Node capability in renderer'));
            else resolve(true);
          } else if (++attempts > 100) { clearInterval(timer); reject(new Error('Frontend initialization timed out')); }
        }, 100);
      })`);
      const before = await apiRequest('/api/project');
      await window.webContents.executeJavaScript(`document.querySelector('#demo').click(); new Promise((resolve,reject)=>{
        let attempts=0; const timer=setInterval(()=>{
          const result=document.querySelector('.result-money');
          if(result && result.textContent==='¥1202.00' && !document.querySelector('#demo-banner').hidden){clearInterval(timer);resolve(true);}
          else if(++attempts>250){clearInterval(timer);reject(new Error('One-click demo did not finish'));}
        },100);
      })`);
      if (await apiRequest('/api/project') !== before) throw new Error('Demo changed real project data');
      console.log(JSON.stringify({ desktop_smoke: 'passed', version: VERSION, platform: process.platform, arch: process.arch, one_click_demo: true }));
      if (process.env.PROMOPATH_DESKTOP_SMOKE_REPORT)
        fs.writeFileSync(process.env.PROMOPATH_DESKTOP_SMOKE_REPORT, JSON.stringify({ passed: true, version: VERSION, title, one_click_demo: true, demo_preserves_project: true }));
      app.quit();
    }
  }).catch(async e => {
    log(e.stack || e.message);
    if (!smoke) dialog.showErrorBox('启动失败', `${e.message}\n数据与日志目录：${dataRoot}`);
    await stopBackend(); app.exit(1);
  });
}
