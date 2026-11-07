# v0.3.1 — Mac 签名修复与最终安装包验收

v0.3.0 的 Mac 打包配置显式跳过签名，且测试只启动构建目录里的应用。用户报告下载后提示“已损坏”。对旧版 DMG 的实际检查发现签名完整性失败：`code has no resources but signature indicates they must be present`。本版修复这一发布缺口，不把构建成功等同于用户安装成功。

## 改动

- Mac 改为显式 ad-hoc 签名，保持 hardened runtime；先为内置 Python、扩展库、Playwright 本机代码签名，再封装应用。
- 新增签名回归测试：最终 DMG 校验、挂载、复制安装、整体与嵌套代码的严格签名检查、架构检查、复制后启动和启动后的签名复查。
- 下载 v0.3.0 DMG 只做签名对照，不执行旧应用；结果保存在附件 installer-validation-mac-*.json。
- 单独记录带 quarantine 属性的 Gatekeeper 评估。**签名完整不等于系统信任，也不等于 Apple 公证。**
- Windows 新增最终 EXE 在含空格路径静默安装后的启动与同步卸载测试；Linux 新增 DEB 和 AppImage 解包后的运行测试。
- 修复快速连续保存时因系统时钟分辨率导致的备份同名覆盖，兼容重启、时钟回拨和旧备份；保留最近 20 个版本。
- 修复 Windows 多命令测试步骤掩盖较早失败的缺陷；任一测试失败即停止，不发布。
- 不更改购物数据路径、不清空数据、不关闭系统安全设置。

## 下载选择与兼容性

Windows x64：windows-x64-setup.exe；Mac Apple 芯片：mac-arm64.dmg；Mac Intel：mac-x64.dmg；Linux x64：AppImage 或 amd64.deb。Source code 不是安装程序。

**Mac 要求 macOS 13 Ventura 或更新版本。** 本版 Electron 45 不支持 macOS 12 及更早系统，配置已明确标注最低系统版本。CI 在 macOS 15 上验收，不代表所有 macOS 13+ 机型均已测试。旧系统请使用满足依赖要求的源码版或升级受支持系统，不建议为兼容而使用失去维护的浏览器内核。

## Mac 首次打开

1. 退出旧应用，将新版 DMG 中的应用拖入“应用程序”并替换旧应用；不要删除 Application Support 中的数据。
2. 核对 SHA256SUMS.txt。安装后的签名可使用 `codesign --verify --deep --strict --verbose=2 /Applications/TaoBaoPriceCalculator.app` 检查。
3. 本版仅有 ad-hoc 完整性签名，没有 Developer ID 证书或 Apple 公证。确认来源和完整性后，可尝试系统设置 → 隐私与安全性 → 仍要打开，按系统提示仅批准此应用。
4. 若仍报告“已损坏”或签名检查失败，请停止启动并报告输出；不要全局关闭 Gatekeeper、SIP 或杀毒保护。

**本版不承诺浏览器下载后无警告双击即用。** 正式的可信分发需要持有者的 Developer ID Application 证书以及 Apple 公证。没有这些凭据，不能伪造系统信任。不要把证书私钥或密码发到聊天、提交到公开仓库。

## 测试边界

安装验证报告会随 Release 一起提供。Mac 的直接可执行文件启动与 Gatekeeper 策略评估是两项独立测试；没有模拟用户 Finder 对话框点击。Windows 未模拟 SmartScreen 交互；Linux 未覆盖全部发行版依赖、DEB 系统安装和 AppImage FUSE 挂载。没有进行淘宝实账号验收。

来源：https://support.apple.com/en-us/102445 · https://www.electron.build/v26/docs/features/code-signing/code-signing-mac/ · https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution · https://www.electronjs.org/blog/electron-44-0 · https://nsis.sourceforge.io/Docs/Chapter3.html
