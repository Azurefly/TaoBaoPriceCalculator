"""Copy release code into an existing local installation, preserving user data and venv."""
from __future__ import annotations
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.data', '.venv', '.env', '.git', '__pycache__', '.pytest_cache', 'test-artifacts'}


def upgrade(target: Path):
    target = target.expanduser().resolve()
    if target == ROOT or ROOT in target.parents or target in ROOT.parents:
        raise ValueError('源与目标不能相同或相互嵌套；请将新版解压到旧目录以外。')
    if not (target/'app'/'main.py').is_file() or not (target/'run.py').is_file():
        raise ValueError('目标不是可识别的旧版PromoPath目录；未复制任何文件。')
    # Do not follow symlink destinations into data or outside the requested install.
    todo=[]
    for source in ROOT.rglob('*'):
        rel=source.relative_to(ROOT)
        if source.is_symlink() or any(part in EXCLUDED for part in rel.parts):
            continue
        if not source.is_file():
            continue
        destination=target/rel
        if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents if parent != target.parent):
            raise ValueError(f'目标含符号链接，请先人工核对：{rel}')
        if not destination.resolve().is_relative_to(target):
            raise ValueError(f'目标路径不在安装目录：{rel}')
        todo.append((source,destination))
    for source,destination in todo:
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
    print(f'已更新 {len(todo)} 个程序文件到 {target}')
    print('未修改 .data、.venv、.env；请启动新版并重新核价，旧报价不会自动升级为有效。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='先停止旧版服务；此脚本只更新代码，不覆盖用户数据。')
    parser.add_argument('target',type=Path,help='旧版promo-path目录')
    args=parser.parse_args()
    try:
        upgrade(args.target)
    except (OSError,ValueError) as exc:
        parser.exit(1,str(exc)+'\n')
