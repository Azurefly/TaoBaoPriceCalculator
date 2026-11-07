"""Ephemeral CI signing keychain; credentials never enter artifacts or logged command lines."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys


def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if p.returncode:
        # Do not interpolate args/stdout/stderr: import/notary commands contain secrets.
        raise RuntimeError('macOS credential operation failed; check certificate/team/credential configuration')
    return p.stdout


def export(values):
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as f:
        for key, value in values.items():
            if '\n' in value or '\r' in value:
                raise ValueError('Unsafe environment value')
            f.write(f'{key}={value}\n')


def prepare():
    required = ['MAC_CERTIFICATE_P12_BASE64', 'MAC_CERTIFICATE_PASSWORD', 'APPLE_ID',
                'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID', 'GITHUB_ENV', 'RUNNER_TEMP']
    missing = [key for key in required if not os.environ.get(key, '').strip()]
    if missing:
        raise ValueError('Missing configuration: ' + ', '.join(missing))
    team = os.environ['APPLE_TEAM_ID']
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise ValueError('Invalid Apple team ID')
    root = Path(os.environ['RUNNER_TEMP']) / ('tbpc-signing-' + secrets.token_hex(8))
    root.mkdir(mode=0o700)
    keychain = root / 'release.keychain-db'
    original = shlex.split(command(['security', 'list-keychains', '-d', 'user']))
    (root / 'original.json').write_text(json.dumps(original))
    # Export before any side effects so the always() cleanup can recover partial setup.
    export({'TBPC_SIGNING_DIR': str(root), 'CSC_KEYCHAIN': str(keychain)})
    password = secrets.token_urlsafe(48)
    p12 = root / 'signing.p12'
    try:
        p12.write_bytes(base64.b64decode(os.environ['MAC_CERTIFICATE_P12_BASE64'], validate=True))
        p12.chmod(0o600)
        command(['security', 'create-keychain', '-p', password, str(keychain)])
        command(['security', 'set-keychain-settings', '-lut', '3600', str(keychain)])
        command(['security', 'unlock-keychain', '-p', password, str(keychain)])
        command(['security', 'import', str(p12), '-k', str(keychain), '-P', os.environ['MAC_CERTIFICATE_PASSWORD'],
                 '-T', '/usr/bin/codesign', '-T', '/usr/bin/security'])
        command(['security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:', '-s',
                 '-k', password, str(keychain)])
        command(['security', 'list-keychains', '-d', 'user', '-s', str(keychain), *original])
        identities = re.findall(r'([0-9A-Fa-f]{40}) "(Developer ID Application: [^"\n]+)"',
                                command(['security', 'find-identity', '-v', '-p', 'codesigning', str(keychain)]))
        identities = [(sha, name) for sha, name in identities if name.endswith('(' + team + ')')]
        if len(identities) != 1:
            raise ValueError('Expected exactly one valid Developer ID Application certificate for the configured team')
        profile = 'tbpc-release'
        command(['xcrun', 'notarytool', 'store-credentials', profile, '--keychain', str(keychain),
                 '--apple-id', os.environ['APPLE_ID'], '--password', os.environ['APPLE_APP_SPECIFIC_PASSWORD'],
                 '--team-id', team])
        export({'TBPC_MAC_CERT_SHA1': identities[0][0], 'TBPC_MAC_IDENTITY': identities[0][1],
                'CSC_NAME': identities[0][1].removeprefix('Developer ID Application: '),
                'TBPC_TEAM_ID': team, 'TBPC_NOTARY_PROFILE': profile,
                'CSC_IDENTITY_AUTO_DISCOVERY': 'true'})
    finally:
        p12.unlink(missing_ok=True)


def cleanup():
    location = os.environ.get('TBPC_SIGNING_DIR')
    if not location:
        return
    root = Path(location).resolve()
    parent = Path(os.environ['RUNNER_TEMP']).resolve()
    if root.parent != parent or not root.name.startswith('tbpc-signing-'):
        raise ValueError('Refusing cleanup outside the dedicated signing directory')
    original = json.loads((root / 'original.json').read_text())
    command(['security', 'list-keychains', '-d', 'user', '-s', *original])
    keychain = root / 'release.keychain-db'
    if keychain.exists():
        command(['security', 'delete-keychain', str(keychain)])
    import shutil
    shutil.rmtree(root)


if __name__ == '__main__':
    if sys.platform != 'darwin':
        raise SystemExit('macOS required')
    if len(sys.argv) != 2 or sys.argv[1] not in ('prepare', 'cleanup'):
        raise SystemExit('Usage: macos_credentials.py prepare|cleanup')
    try:
        {'prepare': prepare, 'cleanup': cleanup}[sys.argv[1]]()
    except Exception as error:
        # Exception text is sanitized by command(); never print subprocess arguments.
        raise SystemExit(str(error)) from None
