"""Bind native validation to exact installer bytes before any Release upload."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from release_policy import channel, expected_installers, sha256, validate_reports


def build(root, version, commit, selected_channel):
    root = Path(root)
    reports = validate_reports(root, version, commit, channel(selected_channel))
    files = sorted(p for p in root.iterdir() if p.is_file())
    allowed = expected_installers(version) | {p.name for p in root.glob('installer-validation-*.json')} | {'package-lock.json'}
    if {p.name for p in files} != allowed or any(p.is_symlink() for p in files):
        raise ValueError('Unexpected or missing release assets; refusing accidental data/credential upload')
    manifest = {'schema_version': 2, 'source_commit': commit, 'version': version, 'channel': selected_channel,
                'created_at': datetime.now(timezone.utc).isoformat(), 'first_launch_tested': False,
                'files': {p.name: sha256(p) for p in files},
                'targets': [{'platform': r['platform'], 'architecture': r['architecture'],
                             'checks': r['checks']} for r in reports]}
    (root / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    files = sorted(p for p in root.iterdir() if p.is_file())
    (root / 'SHA256SUMS.txt').write_text(''.join(sha256(p) + '  ' + p.name + '\n' for p in files), encoding='utf-8')
    preview = selected_channel == 'preview'
    warning = ('**开发自测包：Mac 仅临时签名，未做 Apple 公证，Gatekeeper 可能拒绝。不是免安全拦截的正式安装版。**'
               if preview else '**正式分发候选：自动签名/公证/安装检查通过；仍为草稿，等待独立首次安装验收，不代表已正式发布。**')
    notes = [f'# TaoBaoPriceCalculator {version}', '', warning, '',
             'Windows/macOS/Linux 安装包包含本地运行环境；不含账号数据。不要关闭系统保护或移除隔离属性。', '',
             '| 平台 | 自动安装/运行检查 | 系统信任检查 | 首次安装人工验收 |', '|---|---|---|---|']
    for r in reports:
        trust = '通过' if r['distribution_checks_passed'] else '未通过或不具备正式签名'
        notes.append(f"| {r['platform']} {r['architecture']} | 通过 | {trust} | 未完成 |")
    notes += ['', f'源代码提交：`{commit}`。各附件的 SHA-256 与验收记录可在 Assets 查看。', '',
              '测试范围：Mac 最终 DMG 复制后的签名/公证票据/系统分发检查；Windows 实际 NSIS 安装与卸载；'
              'Linux 实际 DEB 安装/卸载及 AppImage FUSE 挂载与启动。计算和真实淘宝账户采价验收是不同事项。', '',
              'Mac 开发测试包不自动等于可正常首次打开。普通用户请以带有首次安装验收记录的正式发布为准。']
    return manifest, '\n'.join(notes) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('release-assets'))
    parser.add_argument('--notes', type=Path, default=Path('release-notes.md'))
    args = parser.parse_args()
    version = json.loads(Path('desktop/package.json').read_text())['version']
    _, notes = build(args.root, version, os.environ['GITHUB_SHA'], os.environ['TBPC_RELEASE_CHANNEL'])
    args.notes.write_text(notes, encoding='utf-8')
