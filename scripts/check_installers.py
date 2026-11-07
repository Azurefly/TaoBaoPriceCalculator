"""Validate final installers, not just the packager's staging directory.
No Gatekeeper/SIP changes, no quarantine removal, no payment or account access.
Ad-hoc signature integrity and Gatekeeper trust are intentionally separate results.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from check_packaged_desktop import smoke

ROOT = Path(__file__).resolve().parents[1]
MAGIC = {bytes.fromhex(x) for x in ('feedface', 'cefaedfe', 'feedfacf', 'cffaedfe',
                                  'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}


def run(*args, check=True, timeout=180, cwd=None):
    result = subprocess.run([str(x) for x in args], check=False, capture_output=True,
                            text=True, errors='replace', timeout=timeout, cwd=cwd)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}')
    return result


def one(pattern):
    paths = list((ROOT / 'release').glob(pattern))
    if len(paths) != 1:
        raise RuntimeError(f'Expected exactly one {pattern}, found {paths}')
    return paths[0].resolve()


def native_files(root):
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.is_symlink():
            continue
        with p.open('rb') as f:
            if f.read(4) in MAGIC:
                yield p


@contextmanager
def mounted(dmg, parent):
    mount = parent / ('mount-' + uuid.uuid4().hex)
    mount.mkdir()
    run('hdiutil', 'verify', dmg)
    run('hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', mount, dmg)
    try:
        app = mount / 'TaoBaoPriceCalculator.app'
        if not app.is_dir():
            raise RuntimeError('Installer does not contain the expected app')
        yield app
    finally:
        run('hdiutil', 'detach', mount)


def mac(temp):
    report = {'format': 'dmg', 'interactive_finder_launch_tested': False}
    baseline = os.environ.get('PROMOPATH_BASELINE_DMG')
    if baseline:
        with mounted(Path(baseline), temp) as old:
            result = run('codesign', '--verify', '--deep', '--strict', '--verbose=2', old, check=False)
            report['v0_3_0_signature'] = {'valid': result.returncode == 0,
                                         'diagnostic': (result.stdout + result.stderr)[-6000:]}
    with mounted(one('*.dmg'), temp) as source:
        installed = temp / 'installed' / source.name
        installed.parent.mkdir()
        run('ditto', '--rsrc', '--extattr', source, installed)
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', installed)
    files = list(native_files(installed / 'Contents/Resources/backend'))
    if not files:
        raise RuntimeError('No embedded native binaries found')
    for file in files:
        run('codesign', '--verify', '--strict', file)
    main = installed / 'Contents/MacOS/TaoBaoPriceCalculator'
    backend = installed / 'Contents/Resources/backend/TaoBaoPriceCalculator-service'
    expected = 'arm64' if platform.machine() == 'arm64' else 'x86_64'
    for file in (main, backend):
        if expected not in run('lipo', '-archs', file).stdout.split():
            raise RuntimeError(f'Wrong architecture: {file}')
    report['signature_integrity'] = 'passed'
    report['embedded_native_files_verified'] = len(files)
    report['installed_runtime'] = smoke(main)
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', installed)
    report['signature_after_runtime'] = 'passed'
    # Reproduce the downloaded-app policy check on the disposable installed copy.
    # Runtime smoke above deliberately does not claim to test first-run Finder consent.
    run('xattr', '-w', 'com.apple.quarantine', f'0083;{int(time.time()):x};TaoBaoPriceCalculatorCI;{uuid.uuid4()}', installed)
    result = run('spctl', '--assess', '--type', 'execute', '--verbose=4', installed, check=False)
    report['quarantined_gatekeeper'] = {'accepted': result.returncode == 0,
                                       'exit_code': result.returncode,
                                       'diagnostic': (result.stdout + result.stderr)[-6000:]}
    report['publisher_signed'] = False
    report['apple_notarized'] = False
    report['unattended_first_launch_supported'] = False
    report['note'] = 'Ad-hoc preview: a valid signature is not Apple Developer ID trust or notarization.'
    return report


def nsis_command(executable: Path, directory: Path, uninstall: bool = False) -> str:
    # NSIS consumes the rest of the raw command line after /D= or _?=.
    # These options must be last and unquoted, even when the directory has spaces.
    target = str(directory)
    if any(ch in target for ch in ('"', '\r', '\n', '\0')):
        raise ValueError('Invalid NSIS directory')
    return subprocess.list2cmdline([str(executable), '/S']) + (' _?=' if uninstall else ' /D=') + target


def run_nsis(executable: Path, directory: Path, uninstall: bool = False):
    if sys.platform != 'win32':
        raise RuntimeError('NSIS install test requires Windows')
    result = subprocess.run(nsis_command(executable, directory, uninstall),
                            executable=str(executable), shell=False, cwd=directory.parent,
                            capture_output=True, text=True, errors='replace', timeout=240)
    if result.returncode:
        raise RuntimeError(f'NSIS exited {result.returncode}: {result.stdout}\n{result.stderr}')


def windows(temp):
    folder = temp / 'installed with spaces'
    run_nsis(one('*.exe'), folder)
    exe = folder / 'TaoBaoPriceCalculator.exe'
    deadline = time.monotonic() + 30
    while not exe.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    result = smoke(exe)
    uninstall = folder / 'Uninstall TaoBaoPriceCalculator.exe'
    if not uninstall.is_file():
        raise RuntimeError('Missing NSIS uninstaller')
    # A normal NSIS uninstaller returns before its self-copied child exits.
    # Run a copy outside the installation with _?= so we wait for actual completion.
    copied = temp / 'uninstall-check.exe'
    shutil.copy2(uninstall, copied)
    run_nsis(copied, folder, uninstall=True)
    if exe.exists():
        raise RuntimeError('NSIS returned before removing the installed application')
    return {'format': 'nsis', 'silent_install_runtime': result,
            'uninstall_completed': True, 'install_path_with_spaces_tested': True,
            'publisher_signed': False, 'smartscreen_interactive_tested': False}


def linux(temp):
    deb = temp / 'deb'
    deb.mkdir()
    run('dpkg-deb', '-x', one('*.deb'), deb)
    executables = [p for p in (deb / 'opt').rglob('taobao-price-calculator') if p.is_file() and not p.is_symlink()]
    if len(executables) != 1:
        raise RuntimeError(f'Expected one DEB executable: {executables}')
    deb_result = smoke(executables[0])
    extract = temp / 'appimage'
    extract.mkdir()
    image = one('*.AppImage')
    image.chmod(image.stat().st_mode | 0o100)
    run(image, '--appimage-extract', cwd=extract)
    appimage_result = smoke(extract / 'squashfs-root/AppRun')
    return {'deb_extracted_runtime': deb_result, 'appimage_extracted_runtime': appimage_result,
            'deb_system_install_tested': False, 'appimage_fuse_mount_tested': False}


def main():
    target = {'darwin': 'mac', 'win32': 'windows'}.get(sys.platform, 'linux')
    output = ROOT / 'release' / f'installer-validation-{target}-{platform.machine()}.json'
    report = {'platform': sys.platform, 'architecture': platform.machine(), 'passed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='tbpc-install-') as temp:
            report.update({'darwin': mac, 'win32': windows}.get(sys.platform, linux)(Path(temp)))
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
