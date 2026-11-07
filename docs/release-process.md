# v0.3.2：开发自测和正式分发是两条独立发布路径

## 交付边界

这次修改的是打包、签名、验收和发布流程，不会凭空产生 Apple 或 Windows 证书。
没有凭据时可构建开发自测包；不能把这种包标成已通过普通用户下载安装验收。
macOS 显示“已损坏”不能单凭弹框确定原因，也不应要求用户关闭系统保护。

| 检查 | preview 自测包 | distribution 正式分发候选 |
|---|---|---|
| Mac 外层与内嵌代码签名 | 明确临时签名；保留 hardened runtime | 指定团队的 Developer ID Application |
| 动态库权限 | 不附加可执行程序 entitlements | 不附加可执行程序 entitlements |
| Mac 公证 | 未完成，不能承诺 Gatekeeper 放行 | app 公证并附票据，再对最终 DMG 签名、公证并附票据 |
| 最终安装包测试 | 必须通过运行/完整性检查 | 还必须通过系统信任检查 |
| Windows | 可未签名，但必须安装、启动、卸载成功 | 安装器、主程序和服务均须 Authenticode 有效、发布者一致 |
| Linux | 实际 DEB 安装/卸载、AppImage 真正 FUSE 挂载和直接启动 | 相同，不能用解压运行代替 |
| 对外发布 | Pre-release，名称和正文醒目标识未公证 | 先创建 Draft，验收记录通过后才可正式发布 |

自动化报告不再使用一个顶层 `passed` 概括所有结果，分别输出 `preview_checks_passed`、
`distribution_checks_passed` 和 `first_launch_tested`。最后一项在自动脚本中固定为 false。
系统检查通过仍不等于实际首次 Finder 启动已经验证。

## 一、立即可用的自测构建

提交到 main 后 Desktop installers 默认走 preview。也可以从 Actions 手动选择 preview。
所有四种平台/架构通过后发布 `v0.3.2-preview.<run_number>`，五个安装包和四份验证报告一起发布。
不设为 Latest，不覆盖历史资产。安装包中的应用版本是 0.3.2，预览标签包含构建编号。

没有证书不妨碍研发测试，但 Mac 自测包仍可能被 Gatekeeper 阻止。不要删除 quarantine、关闭
Gatekeeper/SIP 或添加 `--no-sandbox` 来把失败结果包装为通过。

## 二、配置正式分发凭据

在 GitHub 仓库 Settings → Environments 新建/保护 `desktop-signing`，添加以下 Secrets：

| Secret | 用途 |
|---|---|
| MAC_CERTIFICATE_P12_BASE64 | 含私钥的 Developer ID Application 证书导出为加密 .p12 后的 base64 |
| MAC_CERTIFICATE_PASSWORD | 上述 .p12 的非空密码 |
| APPLE_ID | 公证用 Apple 账号 |
| APPLE_APP_SPECIFIC_PASSWORD | 应用专用密码，不是 Apple 账号登录密码 |
| APPLE_TEAM_ID | 与证书匹配的 10 位团队 ID |
| WIN_CSC_LINK | electron-builder 支持的 Windows 签名证书输入，例如受支持的 PFX/base64 |
| WIN_CSC_KEY_PASSWORD | Windows 签名证书密码 |

本实现支持可供 electron-builder 使用的 Windows 证书；硬件令牌/云签名需要另行适配，不能将普通
文本或自签名证书当作可信发布者凭据。不要把任何私钥、证书密码、Apple 密码发送到聊天或提交 Git。

Mac 构建创建独立临时 keychain，验证证书类型、有效性和团队，只允许一个匹配身份；公证凭据存入
该 keychain 的临时 profile。结束时恢复原 keychain 列表并删除临时 keychain；凭据不打入安装包。
缺任何必要值就失败，不静默退回临时签名。Windows 构建同样不静默跳过签名。

## 三、生成已签名候选

Actions → Desktop installers → Run workflow → channel 选择 distribution。
此过程必须在 main 上执行。需要环境审批时先由仓库管理员批准。脚本会：

1. 运行核心和发布回归测试，冻结 Python 后端。
2. 从内向外签名；只给可执行文件附加权限，不给动态库附加权限。
3. 公证并附加票据；公证拒绝、超时、stapler 失败均阻止发布。
4. 在最终 DMG 复制品上检查代码签名、嵌入代码、架构、票据、Gatekeeper 与 syspolicy_check。
   macOS 14+ 用 syspolicy_check distribution；旧系统记录 spctl fallback。不清除隔离属性。
5. Windows 做真实静默安装、空格路径运行、签名检查、同步卸载；Linux 做真实系统安装/挂载。
6. 生成逐文件 SHA-256 收据，校验报告必须匹配同一源码提交、版本、渠道和安装包字节。
7. 创建 v0.3.2 Draft，不自动发布为正式版，也不自动成为 Latest。

### 首次安装验收不是自动勾选

在从未运行过此产品的干净机器或恢复快照的 VM 上，用浏览器下载候选安装包，然后按普通用户方式
安装。Mac 从 Finder 打开并测试断网启动；Windows 用安装向导；Linux 测试 DEB 和 AppImage。
均完成一次内置演示计算。禁止通过关闭系统保护获得“通过”。正常互联网来源确认可以出现。

按 `docs/acceptance/TEMPLATE.json` 填写真实结果；不能把示例布尔值批量改成 true 而不测试。
每个安装包必须记录真实 SHA-256、系统版本、测试时间和审核人。复制成
`docs/acceptance/v0.3.2.json`，提交 main。该文档提交本身不会重建安装包。

配置受保护的 `desktop-production` 环境并指定审核人，然后手动运行 Promote verified distribution，
tag 填 v0.3.2。工作流重新下载 Draft 资产，校验安装包、验证报告、源码提交和人工记录，全部符合
才发布并标为 Latest。这里的人工记录是审核人声明，不是程序能替代人工证明的事实。

## 四、测试范围与排错

- 以每次 GitHub Actions 实际状态及附件报告为准；流水线源码存在，不代表签名、公证已经执行。
- `distribution_checks_passed=false` 的自测包不可晋升正式版。
- 修改安装包字节后，旧 SHA-256 和验收记录失效，必须重测。
- 本机 Linux 单元测试不代表 macOS 原生执行测试；证书分支未执行时必须明确说明。
- 证书、公证与运行验收不改变用户数据位置，不触碰淘宝账号、Cookie、订单或付款能力。
- 仍要求在声明支持的 macOS 最低版本上另做兼容性测试；macOS 15 CI 不证明 macOS 13 全覆盖。

## 参考

- Apple: https://developer.apple.com/forums/thread/130560 （干净环境下载、离线、实际首次启动及 syspolicy_check）
- Apple: https://developer.apple.com/documentation/security/creating-distribution-signed-code-for-macos
- electron-builder v26: https://www.electron.build/v26/docs/features/code-signing/code-signing-mac/
- electron-builder v26: https://www.electron.build/v26/docs/features/code-signing/notarization/

构建配置固定使用仓库锁定的 electron-builder v26 参数，不能直接混用 v27 的 mac.sign 字段。
