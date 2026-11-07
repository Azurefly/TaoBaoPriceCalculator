# v0.3.0 · 多端本地桌面预览版

下载本页 **Assets** 中对应系统的安装包，不是 Source code 压缩包。

| 平台 | 安装包 | 使用方式 |
| --- | --- | --- |
| Windows x64 | `*-windows-x64-setup.exe` | 双击安装，开始菜单或桌面启动 |
| macOS Intel | `*-mac-x64.dmg` | 打开后拖入 Applications |
| macOS Apple Silicon | `*-mac-arm64.dmg` | 打开后拖入 Applications |
| Linux x64 | `*.AppImage` | 赋予执行权限后启动；需桌面环境与可用 Chromium 沙箱 |
| Debian / Ubuntu x64 | `*.deb` | 用系统软件安装器安装；推荐 Ubuntu 22.04 及以上 |

安装包内置 Python、本地计算服务和 Electron，不需要安装 Python、Node.js 或运行 pip/npm。首次启动本地计算无需下载依赖。联网淘宝或下载新版仍需网络。

## 新增

- 独立应用窗口、单实例、随机回环端口、应用与数据目录分离。
- 关闭应用时终止本地服务；父进程异常退出也通过管道关闭服务。
- 中文数据路径、轮转日志、菜单打开数据目录、下载新版入口。
- 每个平台原生构建，并执行冻结服务 HTTP 测试、演示计算、桌面窗口冒烟测试；全部通过才发布整套安装包。
- 提供 `SHA256SUMS.txt`、构建提交清单和此次构建共用的 npm 锁文件。

## 数据与升级

- Windows：`%APPDATA%/TaoBaoPriceCalculator/storage/`
- macOS：`~/Library/Application Support/TaoBaoPriceCalculator/storage/`
- Linux：`$XDG_CONFIG_HOME/TaoBaoPriceCalculator/storage/`，未设置时通常为 `~/.config/TaoBaoPriceCalculator/storage/`。

覆盖安装与卸载默认保留数据。旧源码版请先导出项目 JSON，再在桌面版“数据与说明”导入；不自动扫描或搬走旧数据。新版下载后手动覆盖安装，没有静默下载安装代码。

## 必须知道的边界

这是**未配置发布者代码签名/Apple 公证的预览版**，系统可能提示未知发布者。先核验仓库来源与 SHA256，再使用系统提供的正常确认流程；不要关闭系统安全保护。哈希用于核对下载一致性，不能替代发布者签名。

Windows 2022、macOS 15 Intel/ARM、Ubuntu 22.04 x64 是本次 CI 构建/冒烟环境；不声称已经人工验收所有系统版本、安装向导、卸载与升级组合。Linux ARM、Windows ARM、iOS、Android 原生安装包未包含。

桌面壳不改变优化器证据边界：展示价不重复扣券，结果仅对已输入报价和约束成立。未登录用户淘宝、未验收个人账号自动采集，不自动提交订单、付款或退款。实验性浏览器适配器仍需实际页面校准；打包 Playwright 库不等于已包含自动采价能力。许可及配置符合条件时可使用另行安装的 Chrome/Edge，独立 profile 不复用个人默认登录态。
