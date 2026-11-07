# TaoBaoPriceCalculator · 淘宝省钱助手

本地比较淘宝/天猫活动里的合单、拆单和付款顺序。默认使用简单界面：选商品 → 核对价格 → 看付款方案。

## v0.5.0：店铺与组合优惠、购物车分组

首页新增「从淘宝购物车导入」和「店铺与组合优惠」。新增店铺互斥择优、指定商品/搭配数量、独立凑门槛范围、
排除商品、多档满减和精确数量套餐一口价；每笔显示优惠明细。一次用掉券后可用的替代优惠也纳入付款顺序。

购物车工具可在本机启动独立Chrome/Edge，由本人扫码登录；区分淘宝原生分组、当前已读页面和按店铺筛选，
仅导入选中的行。**此接入尚未经过真实淘宝账号验收，页面不匹配需校准；不保证全量读取和即插即用。**
已有平台许可声明要求保留，不从扫码登录推断官方授权。私人券包和全部活动规则不会自动识别。

新页面右上角「一键体验」无需登录：自动展示虚构分组和店铺/组合/跨店优惠，既不改真实清单也不连接淘宝。
详见 [功能范围、使用步骤、规则依据和测试](docs/shopping-rules-cart.md)。

## 第一次打开，直接点「一键体验」

不用登录、不用上传文件、不用配置券、不用选择计算模式。程序自动准备虚构商品和优惠，并展示每一笔怎么购买。
首页原有「模拟完成这一笔」可体验付款步骤；退出后回到原来的清单。**整个体验不改真实数据，不产生订单，也不扣优惠。**

用自己的商品：先填名称或导入购物车，再记录淘宝确认订单页的实际应付金额和已使用优惠，点击「帮我算怎么付更省」。
也可以补充核对后的优惠前货款和活动规则，进行明确标识的规则估算；不是在购物车券后价上再次减券。
结果用商品名称、金额和付款步骤说明，不要求普通用户理解JSON、SKU ID或候选预算。

详细说明：[简单模式与一键体验](docs/simple-mode.md)。旧版全部功能保留在 **更多 → 高级设置与旧版界面**（`/advanced`）。

## 下载安装

到 [GitHub Releases](https://github.com/Azurefly/TaoBaoPriceCalculator/releases) 的 Assets 下载对应安装包，不是 Source code。
源代码版本更新不等于该版本安装包已经完成构建，请以Release实际附件为准。

| 系统 | 文件 |
|---|---|
| Windows x64 | `*-windows-x64-setup.exe` |
| macOS Apple Silicon | `*-mac-arm64.dmg` |
| macOS Intel | `*-mac-x64.dmg` |
| Linux x64 | `*.deb` 或 `*.AppImage` |

安装包内置运行环境，不需手工安装Python或Node.js。当前自动发布走 **preview 开发自测渠道**，
Mac为临时签名且未完成Apple公证，仍可能被系统阻止；本次功能更新没有解决这一分发限制。
正式分发必须另行通过签名、系统信任和首次安装验收。不要关闭系统安全保护。
见 [发行说明](docs/desktop-release.md) 和 [发布验收流程](docs/release-process.md)。不包含iOS/Android原生客户端。

## 数据与使用边界

- 本地保存项目和自动备份；升级保留用户目录。旧版清单从「更多 → 导入以前的清单」导入。
- 展示价只是参考，不从已经券后/红包后的金额再减优惠。实际报价已经扣过定金就不再扣一次。
- 每张券和红包只占用一次。用掉其他优惠后价格是否仍成立，必须明确核对，程序不默认独立。
- 支付仍由本人在淘宝完成。确认真实付款后，才在本程序登记、更新余额并重新核价。
- 仅在已输入的有效报价和约束内比较，不保证全淘宝、全部入口或未来价格的最低价。
- 当前每个购物计划最多28行；数量按SKU行整体处理，不自动拆件，不计未核实的退款收益。

**不是淘宝官方产品，也不是登录后能全自动读取全部优惠的成品。** 普通流程仍需核对实际结算价格。
实验性浏览器适配需满足适用要求并校准页面；不自动提交订单、付款、退款，不绕过验证码。
示例全部虚构；不会打包个人订单、Cookie、`.env`或浏览器登录态。数据目录可从应用菜单打开。

## 开发与测试

源码运行需Python 3.11+。首次安装依赖联网：macOS/Linux `bash start.sh`；Windows双击 `start.bat`。
源码浏览器功能另需 `python -m pip install -r requirements-browser.txt`，并在本机安装Chrome或Edge。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/simple.js
node --check app/static/shopping.js
node --test desktop/tests.cjs desktop/tests-signing.cjs desktop/tests-release.cjs
```

简单主页测试：`python scripts/check_simple_ui.py`（可用 `PROMOPATH_TEST_CHROMIUM` 指定已有Chromium）。
新增购物工具测试：`PYTHONPATH=. python scripts/check_shopping_ui.py`（可用 `TBPC_TEST_CHROMIUM` 指定已有Chromium）。
两种页面测试使用真实前端与FastAPI TestClient桥接；购物车DOM为合成夹具，不访问淘宝。
桌面安装验收另会真实点击首页一键体验，核对结果与体验前后清单一致。测试数量与结果以对应提交记录为准。

[架构与算法](docs/02_架构与最优性.md) · [规则调研](docs/01_规则调研.md) · [桌面构建](docs/desktop-build.md)
