# 桌面发行说明

从 v0.3.2 起，发行说明由通过校验的最终安装包报告生成，不再硬编码“全部通过”。

- preview：临时签名自测包，Mac 未公证，可能被 Gatekeeper 拒绝。不是无拦截安装体验的承诺。
- distribution：Developer ID/Windows 可信签名及安装检查通过后创建 Draft；人工首次安装记录和
  所有安装包 SHA-256 匹配后，才可晋升为正式版。

安装包包含本地计算运行环境，但桌面封装不表示淘宝全量权益采集已完成。
Mac 签名检查、系统信任检查、自动运行检查和真实首次安装是独立项目。
正常的互联网来源首次确认不等于“应用已损坏”。不要删除隔离标记或关闭系统保护来验收。

支持的发行目标：Windows x64、macOS Intel/Apple Silicon、Linux x64 的 DEB 与 AppImage。
macOS 目标最低版本 13.0；最低系统版本上的兼容性仍须如实验收，不用 macOS 15 CI 冒充全部版本测试。

安装后项目数据仍位于用户目录，卸载默认保留数据。旧源码版先导出 JSON，再在桌面版导入。

操作流程、需要的 Secrets 与验收模板见 [release-process.md](release-process.md)。
