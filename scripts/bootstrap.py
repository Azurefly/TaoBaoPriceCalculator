"""Create/check local venv; install only when dependencies actually differ."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def ensure_environment() -> Path:
    if sys.version_info < (3, 11):
        raise RuntimeError('需要 Python 3.11 或更高版本。')
    executable = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.exists():
        print('创建本地运行环境……', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    dependencies = {}
    for line in (ROOT/'requirements.txt').read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if line and not line.startswith('#'):
            name, version = line.split('==', 1)
            dependencies[name] = version
    probe = ('import importlib.metadata as m,json,sys; expected=json.loads(sys.argv[1]); '
             'installed={d.metadata["Name"].lower():d.version for d in m.distributions()}; '
             'sys.exit(0 if all(installed.get(k.lower())==v for k,v in expected.items()) else 1)')
    valid = subprocess.run([str(executable), '-c', probe, json.dumps(dependencies)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not valid:
        print('首次安装或版本变化，需要联网安装依赖……', flush=True)
        subprocess.run([str(executable), '-m', 'pip', 'install', '-r', str(ROOT/'requirements.txt')], check=True)
    else:
        print('依赖已满足，跳过联网安装。', flush=True)
    return executable


def main() -> int:
    try:
        exe=ensure_environment()
        if '--prepare-only' in sys.argv:
            print(str(exe))
            return 0
        return subprocess.call([str(exe), str(ROOT/'run.py')], cwd=ROOT)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'启动失败：{exc}\n请检查Python版本、网络和目录写权限；现有项目数据没有删除。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
