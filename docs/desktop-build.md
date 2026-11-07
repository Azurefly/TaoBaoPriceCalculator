# 桌面构建

详细渠道、凭据、安装验收和晋升流程见 [release-process.md](release-process.md)。
不要把成功构建或本地进程启动等同于浏览器下载后的首次安装成功。

## 本地自测包

需 Python 3.11+、Node.js 22 和目标系统的打包工具；跨平台安装包在对应的原生 GitHub runner 上生成。

```bash
python -m pip install -r requirements-desktop.txt -r requirements-dev.txt
python -m pytest -q
node --test desktop/tests.cjs desktop/tests-signing.cjs desktop/tests-release.cjs
python -m PyInstaller --noconfirm --clean desktop/backend.spec
python scripts/check_packaged_backend.py
cd desktop
npm install
npm run dist
```

`npm run dist` 强制加载 builder.config.cjs；默认 preview。已有 package-lock.json 时用 npm ci。
生产构建需要完整凭据，推荐使用受保护的 GitHub 环境执行，不在用户设备或聊天中传递私钥。

## 最终安装包验证

`python scripts/verify_installers.py` 生成 schema_version=2 的独立运行/分发检查报告。
Mac 和 Windows 必须在原生系统运行。Linux 实际系统安装会修改测试机包管理器，只允许显式启用的
一次性 GitHub runner；不会在普通用户机器上自动安装/卸载测试包。

旧 `check_installers.py` 保留为实现辅助和兼容测试，不再作为发布门槛的最终报告来源。
源码输入应只包含虚构示例；.data、.env、浏览器会话、证书和日志不得进入发行资产。
