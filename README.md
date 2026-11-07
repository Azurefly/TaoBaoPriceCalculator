# TaoBaoPriceCalculator · 淘宝省钱助手

本地比较淘宝/天猫活动里的合单、拆单和付款顺序。**v0.4.0 默认使用简单界面：选商品 → 核对价格 → 看付款方案。**

## 第一次打开，直接点「一键体验」

不用登录、不用上传文件、不用配置券、不用选择计算模式。程序自动准备虚构商品和优惠，并展示每一笔怎么购买。
点击「模拟完成这一笔」体验步骤；退出后回到原来的清单。**整个体验不改真实数据，不产生订单，也不扣优惠。**

用自己的商品：先填名称（价格可稍后填），再记录淘宝确认订单页的实际应付金额和已使用优惠，点击「帮我算怎么付更省」。
结果用商品名称、金额和付款步骤说明，不要求普通用户理解 JSON、SKU ID、候选预算或资源账本。

详细说明：[简单模式与一键体验](docs/simple-mode.md)。旧版全部功能保留在 **更多 → 高级设置与旧版界面**（`/advanced`）。

## 下载安装

到 [GitHub Releases](https://github.com/Azurefly/TaoBaoPriceCalculator/releases) 的 Assets 下载对应安装包，不是 Source code。

| 系统 | 文件 |
|---|---|
| Windows x64 | `*-windows-x64-setup.exe` |
| macOS Apple Silicon | `*-mac-arm64.dmg` |
| macOS Intel | `*-mac-x64.dmg` |
| Linux x64 | `*.deb` 或 `*.AppImage` |

安装包内置运行环境，不需手工安装 Python 或 Node.js。当前自动发布走 **preview 开发自测渠道**，
Mac 为临时签名且未完成 Apple 公证，仍可能被系统阻止；界面改版不等于解决了签名、公证和首次安装问题。
正式分发必须另行通过签名、系统信任和首次安装验收。不要关闭系统安全保护。
见 [发行说明](docs/desktop-release.md) 和 [发布验收流程](docs/release-process.md)。不包含 iOS/Android 原生客户端。

## 数据与使用边界

- 本地保存项目和自动备份；升级保留用户目录。旧版清单从「更多 → 导入以前的清单」导入。
- 展示价只是参考，不从已经券后/红包后的金额再减优惠。实际报价已经扣过定金就不再扣一次。
- 每张券和红包只占用一次。用掉其他优惠后价格是否仍成立，必须明确核对，程序不默认独立。
- 支付仍由本人在淘宝完成。确认真实付款后，才在本程序登记、更新余额并重新核价。
- 仅在已输入的有效报价和约束内比较，不保证全淘宝、全部入口或未来价格的最低价。
- 退款情景、复杂权益、规则模拟和实验性浏览器适配保留在高级界面。

**不是淘宝官方产品，也不是登录后能全自动读取全部优惠的成品。** 当前普通流程需本人记录结算价格。
实验性浏览器适配仍需满足平台适用要求并校准页面；不自动下单、付款、退款，不绕过验证码。
示例全部虚构；不会打包个人订单、Cookie、`.env` 或浏览器登录态。数据目录可从应用菜单打开。

## 开发与测试

源码运行需 Python 3.11+。首次安装依赖联网：macOS/Linux `bash start.sh`；Windows 双击 `start.bat`。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/simple.js
node --test desktop/tests.cjs desktop/tests-signing.cjs desktop/tests-release.cjs
```

页面测试：`python scripts/check_simple_ui.py`（需要 Playwright Chromium；也可用 `PROMOPATH_TEST_CHROMIUM` 指定已有 Chromium）。
旧命令 `scripts/check_ui.py` 会转入这个默认界面测试。浏览器测试使用真实前端与 FastAPI TestClient 桥接，不访问淘宝。
桌面安装验收另会真实点击一键体验，核对结果与体验前后清单一致。

[架构与算法](docs/02_架构与最优性.md) · [规则调研](docs/01_规则调研.md) · [桌面构建](docs/desktop-build.md)
