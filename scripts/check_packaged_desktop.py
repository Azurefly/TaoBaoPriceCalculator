"""Run the packaged renderer. --executable also tests files installed/extracted from installers.
This runtime check is NOT a Gatekeeper or notarization check.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def smoke(executable: Path) -> dict:
    executable = executable.resolve()
    if not executable.is_file():
        raise RuntimeError(f'Missing packaged executable: {executable}')
    with tempfile.TemporaryDirectory(prefix='tbpc-smoke-') as temp:
        report = Path(temp) / 'result.json'
        env = {**os.environ, 'PROMOPATH_DESKTOP_SMOKE': '1',
               'PROMOPATH_DESKTOP_TEST_DATA': str(Path(temp) / 'data'),
               'PROMOPATH_DESKTOP_SMOKE_REPORT': str(report)}
        result = subprocess.run([str(executable)], env=env, timeout=100, capture_output=True, text=True)
        if result.returncode != 0 or not report.is_file():
            logs = list((Path(temp) / 'data').rglob('*.log'))
            detail = '\n'.join(p.read_text(errors='replace')[-8000:] for p in logs)
            raise RuntimeError(f'Packaged smoke failed ({result.returncode})\n{result.stdout}\n{result.stderr}\n{detail}')
        data = json.loads(report.read_text())
        if data.get('passed') is not True:
            raise RuntimeError(f'Renderer did not pass: {data}')
        return data


def default_executable() -> Path:
    release = Path(__file__).resolve().parents[1] / 'release'
    if sys.platform == 'win32':
        return release / 'win-unpacked/TaoBaoPriceCalculator.exe'
    if sys.platform == 'darwin':
        paths = list(release.glob('mac*/TaoBaoPriceCalculator.app/Contents/MacOS/TaoBaoPriceCalculator'))
        if len(paths) != 1:
            raise RuntimeError(f'Expected one app executable, found {paths}')
        return paths[0]
    return release / 'linux-unpacked/taobao-price-calculator'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    print(json.dumps(smoke(args.executable or default_executable()), ensure_ascii=False))


if __name__ == '__main__':
    main()
