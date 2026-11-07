# 桌面构建与维护

## 架构

`Electron main.cjs → 内置 PyInstaller 服务 → FastAPI + 原有中文页面`。

计算引擎保留 PromoPath 0.2.0 接口，桌面发行版本为 0.3.0。普通 `python run.py` 开发方式不变。独立桌面入口在导入 FastAPI 前选择用户数据目录，不写安装目录，也不通过旧启动脚本联网安装依赖。

仅监听 `127.0.0.1` 随机端口，套接字先绑定并保持打开再交给 uvicorn，不存在先探测再绑定的窗口。父进程通过随机令牌与服务通信，退出接口受既有令牌/Origin 防护。渲染器关闭 Node 集成，开启上下文隔离和沙箱；无 preload 特权接口，不允许远程页面加载到应用窗口。

## 本地构建（开发者才需要）

需要对应系统原生环境、Python 3.12、Node.js 22；不可在 Linux 构建 macOS 的 Python 可执行文件。

```bash
python -m pip install -r requirements-desktop.txt -r requirements-dev.txt
python -m pytest -q
node --test desktop/tests.cjs
python -m PyInstaller --noconfirm --clean desktop/backend.spec
python scripts/check_packaged_backend.py
cd desktop
npm install
npm run dist
```

构建输出在仓库 `release/`。目标由当前平台决定；Mac 请分别使用 Intel / Apple Silicon 原生环境，不能仅给另一架构的 Electron 混入当前架构的 Python。

使用官方 CI 时先生成一个 npm 锁文件，全部平台统一 `npm ci`，锁文件同时附在 Release；版本依赖已精确固定，但后续重新生成锁文件仍可能改变传递依赖，不宣称跨时间位级可复现。可把成功构建的锁文件纳入后续源码维护。

## 发布

`.github/workflows/desktop-release.yml` 在主分支桌面/应用代码变更或手动执行时启动。四个构建任务（Windows x64、macOS x64、macOS ARM64、Linux x64）全部通过且安装包齐全才发布预览 Release。需要 Actions 可用，工作流发布任务获得 `contents: write`。

修改版本时同时更新 `desktop/package.json`、`desktop/service.py` 和对应测试中的版本断言。发布版本存在时不覆盖；不能用同一个版本号替换用户已下载的软件。

## 验证分层

1. 原有核心/API 测试以及桌面后端进程测试。
2. 冻结后二进制自检、真实 HTTP 访问、拒绝未鉴权、完整演示计算与退出。
3. 启动已打包 Electron，检查页面实际渲染及本地接口，然后退出。
4. Windows 安装向导、Gatekeeper/SmartScreen 提示、升级/卸载和淘宝真实采价仍需在目标用户环境验收。前三层不能冒充第四层。

## 安全与许可

不自动下载/执行新版，不绕过操作系统安全提示；证书未提供时不声称已签名或公证。用户数据、项目备份、浏览器 profile 均不进入安装包。自动化淘宝访问功能的原有启用条件不因桌面封装而取消。

参考：
- https://www.electronjs.org/docs/latest/tutorial/security
- https://www.pyinstaller.org/en/stable/
- https://www.electron.build/v26/docs/targets/
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
