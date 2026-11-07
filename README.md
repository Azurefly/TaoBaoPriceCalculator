# TaoBaoPriceCalculator

基于 **PromoPath 0.2.0** 的本地淘宝/天猫促销付款路径优化器。支持用活动规则和真实结算报价比较拆单、合单及付款顺序；不绑定双十一，也不把预估展示价当作原价重复扣券。

> 本项目是可运行的本地原型，不是淘宝官方产品。尚未完成淘宝实账号的全量权益自动采集与端到端验收，不提供自动提交订单、支付、退款或验证码绕过。

## 快速启动

需要 Python 3.11+；首次安装依赖需要联网。

macOS / Linux：

```bash
bash start.sh
```

Windows：双击 `start.bat`。也可手动运行：

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

浏览器打开 `http://127.0.0.1:8765`。仅绑定本机地址，不建议改成公网服务。

## 使用流程

点击“运行演示” → “结算报价”生成规则模拟报价 → “优化付款路径”选择规则模式 → 开始优化。严格模式会排除模拟报价，这是证据约束，不是故障。

使用自己的数据时，逐件录入商品、已付定金及已确认的计价基数；每张真实券设置唯一资源ID；录入结算页中“仍需支付”的现金、完整权益消耗、有效期及证据备注，再比较付款路径。付款后只登记真实已完成的付款，重新核对剩余权益、采价并重算。

## 功能

- 可配置固定满减、折扣封顶、每满减、件数门槛、互斥组、渠道与有效期。
- 对共享券、红包和购物金建立资源账本，防止跨订单重复使用。
- 门槛附近候选、分散分组、补集及采样；比较逐件、合并、自由拆合三种策略。
- 检查每笔预计完成付款时的报价与权益有效性；确定性返券支持顺序依赖。
- 明确配置每店运费、包邮门槛；逐件检查定金，拒绝跨商品抵消异常。
- 登记实际付款、项目版本冲突保护、20份自动备份、付款清单导出。
- 单独核算退款成本，不把未经确认的退款收益当成确定优惠。

“最优”仅指已输入有效报价、资源和时间约束内的结果。候选不全、搜索预算耗尽、未采价入口及未来价格均不在全局最优证明内。

## 隐私与公开版调整

本仓库为公开发布版。`examples/historical.json` 保留兼容路径，但内容已经替换为**完全虚构的测试商品、金额和优惠资源**。没有上传个人订单、原始购物截图、浏览器登录态、`.env`、`.data` 或虚拟环境。

运行数据默认保存在 `.data/`，独立浏览器会话保存在 `.data/browser-profile/`。它们已加入 `.gitignore`，请不要手动上传。

## 可选浏览器适配

适配器默认关闭，需符合平台适用要求并校准实际购物车与结算页选择器。它只能采集报价草稿，不能自动证明完整优惠账本。

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

已有相关许可的部署者可设置 `TAOBAO_PLATFORM_PERMISSION_REFERENCE`；该值只是许可引用记录，不会生成或验证官方许可。可选设置 `PROMOPATH_BROWSER_CHANNEL=chrome` 或 `msedge`。必须使用独立profile，不复用或上传个人默认浏览器配置。

配置示例：`examples/browser_config.template.json`。页面结构变化、验证码、SKU/数量不符或金额不唯一时应停止，不进行绕过。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/app.js
python -m compileall -q app run.py
```

可选UI烟雾测试：`python scripts/check_ui.py`，需要可用的Playwright Chromium。该测试使用内存页面与FastAPI TestClient桥接，不登录淘宝。

公开发布整理后本地核心及API测试结果见 `docs/03_测试与验收.md`，这不等于淘宝真实账号端到端验收。

## 目录

```text
app/models.py            数据口径、证据与资源约束
app/pricing.py           规则模拟与候选生成
app/optimizer.py         资源/时间约束下的付款路径搜索
app/workflow.py          策略比较、诊断、实际付款账本
app/store.py             版本校验与备份恢复
app/refunds.py           退款情景核算
app/adapters/browser.py  实验性浏览器适配
app/static/              中文网页
examples/                合成演示、虚构复盘与JSON Schema
 tests/                  核心与API测试
scripts/                 启动辅助、升级、基准与UI检查
docs/                    调研、架构、发布测试说明
sources.json             调研来源及适用范围
```

旧版本升级：在新版目录运行 `python scripts/upgrade.py "/旧版/promo-path"`。保留旧目录的 `.data`、`.venv`、`.env`；旧报价仍需重新核实。
