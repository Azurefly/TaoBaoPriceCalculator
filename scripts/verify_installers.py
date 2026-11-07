"""Validate final installers in disposable native runners; never disable system security.
The report deliberately has no ambiguous top-level `passed` field.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from release_policy import channel, evaluate, sha256

ROOT = Path(__file__).resolve().parents[1]


def assess(tool, args):
    p = subprocess.run([tool, *map(str, args)], capture_output=True, text=True, errors='replace', timeout=240)
    return {'accepted': p.returncode == 0, 'exit_code': p.returncode,
            'detail': (p.stdout + p.stderr)[-4000:]}


def mac(temp, report):
    from check_installers import mounted, native_files, one, run
    from check_packaged_desktop import smoke
    checks, details = report['checks'], report['details']
    dmg = one('*.dmg')
    with mounted(dmg, temp) as source:
        app = temp / 'installed' / source.name
        app.parent.mkdir()
        run('ditto', '--rsrc', '--extattr', source, app)
        runtime_app = app
        if report['channel'] == 'preview':
            # Two copies of identical signed code: test dev runtime separately from downloaded-app policy.
            # Never remove quarantine from the policy copy to make it run.
            runtime_app = temp / 'runtime' / source.name
            runtime_app.parent.mkdir()
            run('ditto', '--rsrc', '--extattr', source, runtime_app)
            details['runtime_scope'] = 'build-origin copy; NOT downloaded first-launch acceptance'
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', app)
    checks['signature_integrity'] = True
    native = list(native_files(app / 'Contents/Resources/backend'))
    if not native:
        raise RuntimeError('No native backend binaries found')
    for p in native:
        run('codesign', '--verify', '--strict', p)
    checks['embedded_native_signatures'] = True
    details['embedded_native_files_verified'] = len(native)
    main = app / 'Contents/MacOS/TaoBaoPriceCalculator'
    backend = app / 'Contents/Resources/backend/TaoBaoPriceCalculator-service'
    arch = 'arm64' if report['architecture'] == 'arm64' else 'x86_64'
    checks['architecture'] = all(arch in run('lipo', '-archs', p).stdout.split() for p in (main, backend))
    signature = run('codesign', '-d', '--verbose=4', app)
    signature_text = signature.stdout + signature.stderr
    checks['developer_id'] = 'Authority=Developer ID Application:' in signature_text
    checks['expected_team'] = bool(os.environ.get('TBPC_TEAM_ID')) and (
        'TeamIdentifier=' + os.environ.get('TBPC_TEAM_ID', '') in signature_text.splitlines())
    details['signature_identity'] = signature_text[-4000:]
    for key, target in [('app_staple', app), ('dmg_staple', dmg)]:
        details[key] = assess('xcrun', ['stapler', 'validate', target])
        checks[key] = details[key]['accepted']
    # Quarantine is added BEFORE policy checks and runtime; no removal or global overrides.
    run('xattr', '-w', 'com.apple.quarantine', f'0083;{int(time.time()):x};TBPC-CI;{uuid.uuid4()}', app)
    details['gatekeeper'] = assess('spctl', ['--assess', '--type', 'execute', '--verbose=4', app])
    checks['gatekeeper'] = details['gatekeeper']['accepted']
    if shutil.which('syspolicy_check'):
        details['distribution_policy'] = assess('syspolicy_check', ['distribution', app])
    else:
        details['distribution_policy'] = {**details['gatekeeper'], 'fallback': 'spctl; less accurate on pre-macOS-14'}
    checks['distribution_policy'] = details['distribution_policy']['accepted']
    details['dmg_policy'] = assess('spctl', ['--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=4', dmg])
    checks['dmg_policy'] = details['dmg_policy']['accepted']
    if report['channel'] == 'distribution':
        required = ['developer_id', 'expected_team', 'app_staple', 'dmg_staple', 'gatekeeper', 'distribution_policy', 'dmg_policy']
        if any(checks.get(key) is not True for key in required):
            raise RuntimeError('Distribution trust gate failed; refusing to run or publish candidate')
    details['installed_runtime'] = smoke(runtime_app / 'Contents/MacOS/TaoBaoPriceCalculator')  # Not Finder consent.
    checks['installed_runtime'] = details['installed_runtime'].get('passed') is True
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', runtime_app)
    checks['signature_after_runtime'] = True


def authenticode(file):
    script = "$s=Get-AuthenticodeSignature -LiteralPath $env:TBPC_VERIFY_FILE; " \
             "@{status=[string]$s.Status;thumbprint=[string]$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress"
    p = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                       env={**os.environ, 'TBPC_VERIFY_FILE': str(file)}, capture_output=True,
                       text=True, errors='replace', timeout=120, check=True)
    return json.loads(p.stdout)


def windows(temp, report):
    from check_installers import one, run_nsis
    from check_packaged_desktop import smoke
    checks, details = report['checks'], report['details']
    folder = temp / 'installed with spaces'
    installer = one('*.exe')
    run_nsis(installer, folder)
    main = folder / 'TaoBaoPriceCalculator.exe'
    try:
        deadline = time.monotonic() + 30
        while not main.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        files = {'installer': installer, 'main': main,
                 'backend': folder / 'resources/backend/TaoBaoPriceCalculator-service.exe'}
        for label, file in files.items():
            details['signature_' + label] = authenticode(file)
            checks['authenticode_' + label] = details['signature_' + label].get('status') == 'Valid'
        thumbs = {details['signature_' + label].get('thumbprint') for label in files}
        checks['same_publisher'] = len(thumbs) == 1 and bool(next(iter(thumbs)))
        if report['channel'] == 'distribution' and not all(checks.get(k) is True for k in
                ['authenticode_installer', 'authenticode_main', 'authenticode_backend', 'same_publisher']):
            raise RuntimeError('Windows publisher signature validation failed')
        details['installed_runtime'] = smoke(main)
        checks['installed_runtime'] = details['installed_runtime'].get('passed') is True
        checks['install_path_with_spaces'] = True
    finally:
        uninstaller = folder / 'Uninstall TaoBaoPriceCalculator.exe'
        if uninstaller.is_file():
            copied = temp / 'uninstall-check.exe'
            shutil.copy2(uninstaller, copied)
            run_nsis(copied, folder, uninstall=True)
            checks['uninstall_completed'] = not main.exists()


def test_fuse_mount(image):
    proc = subprocess.Popen([str(image), '--appimage-mount'], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    def read():
        for line in proc.stdout:
            lines.put(line.strip())
    threading.Thread(target=read, daemon=True).start()
    try:
        mount = Path(lines.get(timeout=30))
        if not mount.is_absolute() or not os.path.ismount(mount):
            raise RuntimeError('AppImage did not create a real mount')
        escaped = str(mount).replace(' ', '\\040')
        entries = Path('/proc/self/mountinfo').read_text().splitlines()
        if not any(' ' + escaped + ' ' in entry and ' - fuse' in entry for entry in entries):
            raise RuntimeError('AppImage FUSE mount not observed')
        return True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
        proc.stdout.close(); proc.stderr.close()


def linux(temp, report):
    from check_installers import one, run
    from check_packaged_desktop import smoke
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('TBPC_SYSTEM_INSTALL_TESTS') != '1':
        raise RuntimeError('System installation tests require an explicitly enabled disposable CI runner')
    if os.getuid() == 0:
        raise RuntimeError('Desktop runtime tests must not run as root')
    if os.environ.get('APPIMAGE_EXTRACT_AND_RUN'):
        raise RuntimeError('Extraction fallback is not a FUSE installation test')
    checks, details = report['checks'], report['details']
    deb = one('*.deb')
    package = run('dpkg-deb', '--field', deb, 'Package').stdout.strip()
    if package != 'taobao-price-calculator':
        raise RuntimeError('Unexpected system package name')
    status = run('dpkg-query', '-W', '-f=${Status}', package, check=False)
    if status.returncode == 0:
        raise RuntimeError('Refusing to replace a pre-existing system installation')
    try:
        run('sudo', '-n', 'apt-get', '-o', 'DPkg::Lock::Timeout=60', 'install', '--no-install-recommends', '-y', deb, timeout=360)
        paths = run('dpkg-query', '-L', package).stdout.splitlines()
        binaries = {Path(p).resolve() for p in paths if p.endswith('/taobao-price-calculator') and Path(p).is_file()}
        if len(binaries) != 1:
            raise RuntimeError('Expected one installed desktop executable')
        main = next(iter(binaries))
        details['deb_installed_runtime'] = smoke(main)
        checks['deb_system_install'] = details['deb_installed_runtime'].get('passed') is True
        checks['installed_runtime'] = checks['deb_system_install']
    finally:
        run('sudo', '-n', 'apt-get', '-o', 'DPkg::Lock::Timeout=60', 'remove', '-y', package, timeout=180)
        state = run('dpkg-query', '-W', '-f=${Status}', package, check=False)
        checks['deb_uninstall'] = state.returncode != 0 or state.stdout.strip() != 'install ok installed'
    image = one('*.AppImage')
    image.chmod(image.stat().st_mode | 0o100)
    checks['appimage_fuse_mount'] = test_fuse_mount(image)
    details['appimage_direct_runtime'] = smoke(image)
    checks['appimage_direct_runtime'] = details['appimage_direct_runtime'].get('passed') is True


def main():
    selected = channel(os.environ.get('TBPC_RELEASE_CHANNEL', 'preview'))
    arch = {'AMD64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64'}.get(platform.machine(), platform.machine())
    version = json.loads((ROOT / 'desktop/package.json').read_text())['version']
    extensions = {'darwin': ('.dmg',), 'win32': ('.exe',), 'linux': ('.AppImage', '.deb')}[sys.platform]
    artifacts = sorted(p for p in (ROOT / 'release').iterdir() if p.is_file() and p.suffix in extensions)
    report = {'schema_version': 2, 'version': version, 'source_commit': os.environ.get('GITHUB_SHA', 'local'),
              'platform': sys.platform, 'architecture': arch, 'channel': selected, 'checks': {}, 'details': {},
              'artifact_sha256': {p.name: sha256(p) for p in artifacts}}
    output = ROOT / 'release' / f'installer-validation-{sys.platform}-{arch}.json'
    try:
        if not artifacts:
            raise RuntimeError('No final installer artifacts found')
        with tempfile.TemporaryDirectory(prefix='tbpc-install-') as temp:
            {'darwin': mac, 'win32': windows, 'linux': linux}[sys.platform](Path(temp), report)
        report['checks']['artifact_integrity'] = all(sha256(p) == report['artifact_sha256'][p.name] for p in artifacts)
        result = evaluate(report)
        if not result['preview_checks_passed' if selected == 'preview' else 'distribution_checks_passed']:
            raise RuntimeError('Installer acceptance checks failed')
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        report.update(evaluate(report))
        report['first_launch_note'] = 'Automated runtime/trust checks only; fresh-machine browser/Finder acceptance is separate.'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
