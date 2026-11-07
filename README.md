# TaoBaoPriceCalculator · 淘宝优惠计算器

**多端本地桌面版**：比较优惠活动中的拆单、合单与付款顺序；支持普通促销、618、双十一、品牌和直播间活动配置，不把券后展示价当原价重复扣券。

## 下载安装

前往 [GitHub Releases](https://github.com/Azurefly/TaoBaoPriceCalculator/releases)，下载 **Assets** 中对应安装包（不是 Source code）：

| 系统 | 文件 |
| --- | --- |
| Windows x64 | `*-windows-x64-setup.exe` |
| macOS Intel | `*-mac-x64.dmg` |
| macOS Apple Silicon | `*-mac-arm64.dmg` |
| Linux x64 | `*.AppImage` 或 `*.deb` |

**安装包内置 Python、桌面窗口和本地服务，不需要手动安装开发环境。** 安装包只会在四个平台构建与烟雾测试全部通过后发布；Release 不含附件时，不能把源码压缩包当安装包。

当前为未配置发布者签名和 Apple 公证的预览发行。系统可能提示未知发布者；先核验来源及 `SHA256SUMS.txt`，不要关闭系统安全保护。兼容范围和使用方法见 [桌面发行说明](docs/desktop-release.md)。不包含 iOS/Android 原生客户端。

## 使用与数据

打开应用 → 运行演示 → 结算报价中按规则模拟 → 优化付款路径选择规则模式。自己的订单应录入实际结算金额和完整权益消耗；严格模式不会把模拟结果当真实支付价格。

数据保存在独立用户目录，菜单“应用 → 打开数据目录”可查看。覆盖安装和卸载默认保留数据。旧版先导出项目 JSON，再在桌面版导入。下载新版本后手动覆盖安装，不静默下载或执行更新。

## 已有功能

- 满减、每满减、折扣封顶、件数门槛、互斥、范围与有效期配置。
- 每张券/红包唯一账本，避免多个单品展示价重复兑现同一优惠。
- 接近门槛组合、分散分组及补集；比较逐件、合并、自由拆合。
- 付款完成时点的权益检查、确定性返券依赖、运费和定金核对。
- 实际付款登记、版本冲突保护、20 份自动备份、退款情景与导出。

“最优”仅指输入的有效报价、资源与时间约束内的结果，不保证淘宝全部入口和未来价格的全局最低价。

## 淘宝接入边界

**不是淘宝官方产品，不是已完成真实账号全量采价的成品。** 实验性浏览器适配默认关闭，仍需满足适用要求并校准页面；不自动下单、支付、退款，不绕过验证码。桌面包内含 Playwright 库，可配置使用本机 Chrome/Edge，但不内置淘宝登录态。公共示例完全虚构，不含个人订单或截图。

## 源码运行（开发者）

需要 Python 3.11+，首次安装依赖需联网。macOS/Linux 运行 `bash start.sh`；Windows 双击 `start.bat`。也可：

```bash
python -m venv .venv
# 激活虚拟环境后
python -m pip install -r requirements.txt
python run.py
```

源码界面默认 `http://127.0.0.1:8765`；桌面版使用自动分配的本机端口，不需要填写地址。

## 构建和测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test desktop/tests.cjs
```

[桌面构建与发布](docs/desktop-build.md) · [核心算法说明](docs/02_架构与最优性.md) · [规则调研](docs/01_规则调研.md)
