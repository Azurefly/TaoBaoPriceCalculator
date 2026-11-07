"""Launch an actual unpacked Electron distribution and require a rendered-page report."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
release = root / 'release'
if sys.platform == 'win32':
    executable = release / 'win-unpacked/TaoBaoPriceCalculator.exe'
elif sys.platform == 'darwin':
    paths = list(release.glob('mac*/TaoBaoPriceCalculator.app/Contents/MacOS/TaoBaoPriceCalculator'))
    if len(paths) != 1:
        raise SystemExit(f'Expected one app executable, found {paths}')
    executable = paths[0]
else:
    executable = release / 'linux-unpacked/taobao-price-calculator'
if not executable.is_file():
    raise SystemExit(f'Missing packaged executable: {executable}')
with tempfile.TemporaryDirectory(prefix='tbpc-smoke-') as temp:
    report = Path(temp) / 'result.json'
    env = {**os.environ, 'PROMOPATH_DESKTOP_SMOKE': '1', 'PROMOPATH_DESKTOP_TEST_DATA': str(Path(temp) / 'data'),
           'PROMOPATH_DESKTOP_SMOKE_REPORT': str(report)}
    cmd = [str(executable)]
    # Keep Chromium sandbox enabled, including in CI. Do not run as root.
    result = subprocess.run(cmd, env=env, timeout=100, capture_output=True, text=True)
    if result.returncode != 0 or not report.is_file():
        logs = list((Path(temp) / 'data').rglob('*.log'))
        detail = '\n'.join(p.read_text(errors='replace')[-8000:] for p in logs)
        raise SystemExit(f'Packaged smoke failed ({result.returncode})\n{result.stdout}\n{result.stderr}\n{detail}')
    data = json.loads(report.read_text())
    assert data['passed'] is True
    print(json.dumps(data, ensure_ascii=False))
