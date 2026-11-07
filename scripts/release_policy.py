"""Pure publication policy. 'Runs' and 'trusted distribution' are separate, fail-closed results."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime, timezone

CHANNELS = ('preview', 'distribution')
TARGETS = {('darwin', 'x64'), ('darwin', 'arm64'), ('win32', 'x64'), ('linux', 'x64')}


def channel(value):
    if value not in CHANNELS:
        raise ValueError('Unknown release channel')
    return value


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def evaluate(report):
    checks = report.get('checks', {})
    os_name = report.get('platform')
    common = ['installed_runtime', 'artifact_integrity']
    additional = {
        'darwin': ['signature_integrity', 'signature_after_runtime', 'embedded_native_signatures', 'architecture'],
        'win32': ['uninstall_completed', 'install_path_with_spaces'],
        'linux': ['deb_system_install', 'deb_uninstall', 'appimage_fuse_mount', 'appimage_direct_runtime'],
    }
    trust = {
        'darwin': ['developer_id', 'expected_team', 'app_staple', 'dmg_staple',
                   'gatekeeper', 'distribution_policy', 'dmg_policy'],
        'win32': ['authenticode_installer', 'authenticode_main', 'authenticode_backend', 'same_publisher'],
        'linux': [],
    }
    if os_name not in additional:
        raise ValueError('Unknown validation platform')
    failed = [key for key in common + additional[os_name] if checks.get(key) is not True]
    trust_failed = [key for key in trust[os_name] if checks.get(key) is not True]
    # A Linux preview may pass all automated checks; that does not promote other platforms.
    return {'preview_checks_passed': not failed,
            'distribution_checks_passed': not failed and not trust_failed,
            'failed_runtime_checks': failed, 'failed_distribution_checks': trust_failed,
            'first_launch_tested': False}


def expected_installers(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Version must be major.minor.patch')
    prefix = f'TaoBaoPriceCalculator-{version}-'
    return {prefix + suffix for suffix in ('windows-x64-setup.exe', 'mac-arm64.dmg', 'mac-x64.dmg',
                                         'linux-x86_64.AppImage', 'linux-amd64.deb')}


def validate_reports(root, version, commit, selected_channel):
    channel(selected_channel)
    root = Path(root)
    reports = []
    targets = set()
    covered = set()
    for p in sorted(root.glob('installer-validation-*.json')):
        report = json.loads(p.read_text())
        if report.get('schema_version') != 2 or report.get('source_commit') != commit or report.get('version') != version:
            raise ValueError('Stale or unsupported validation report: ' + p.name)
        if report.get('error'):
            raise ValueError('Validation reported an execution error')
        if report.get('channel') != selected_channel:
            raise ValueError('Validation channel mismatch')
        target = (report.get('platform'), report.get('architecture'))
        if target not in TARGETS or target in targets:
            raise ValueError('Missing, duplicate, or unsupported target report')
        targets.add(target)
        assessment = evaluate(report)  # Never trust the serialized passed flag.
        key = 'preview_checks_passed' if selected_channel == 'preview' else 'distribution_checks_passed'
        if not assessment[key]:
            raise ValueError(f'{target} failed {selected_channel} checks: {assessment}')
        artifacts = report.get('artifact_sha256', {})
        permitted = {name for name in expected_installers(version) if
                     (target == ('darwin', 'arm64') and name.endswith('mac-arm64.dmg')) or
                     (target == ('darwin', 'x64') and name.endswith('mac-x64.dmg')) or
                     (target == ('win32', 'x64') and name.endswith('windows-x64-setup.exe')) or
                     (target == ('linux', 'x64') and (name.endswith('.AppImage') or name.endswith('.deb')))}
        if set(artifacts) != permitted:
            raise ValueError('Report must cover its exact installer set')
        for name, digest in artifacts.items():
            path = root / name
            if path.is_symlink() or not path.is_file() or sha256(path) != digest:
                raise ValueError('Installer changed after validation: ' + name)
            if name in covered:
                raise ValueError('Duplicate artifact coverage')
            covered.add(name)
        report.update(assessment)
        reports.append(report)
    if targets != TARGETS or covered != expected_installers(version):
        raise ValueError('Four platform reports and all five installers are required')
    return reports


def validate_acceptance(root, evidence):
    root = Path(root)
    manifest = json.loads((root / 'build-manifest.json').read_text())
    if manifest.get('schema_version') != 2 or manifest.get('channel') != 'distribution':
        raise ValueError('Only a distribution candidate can be promoted')
    version, commit = manifest['version'], manifest['source_commit']
    if evidence.get('schema_version') != 1 or evidence.get('tag') != 'v' + version or evidence.get('source_commit') != commit:
        raise ValueError('Acceptance record does not match candidate')
    reviewer = evidence.get('reviewer', '').strip()
    if not reviewer or reviewer.lower() in ('todo', 'replace', 'example'):
        raise ValueError('A real named reviewer is required')
    when = datetime.fromisoformat(evidence.get('tested_at', ''))
    built = datetime.fromisoformat(manifest['created_at'])
    if when.tzinfo is None or built.tzinfo is None or when < built or when > datetime.now(timezone.utc):
        raise ValueError('Invalid acceptance timestamp')
    for name, digest in manifest.get('files', {}).items():
        if Path(name).name != name or name.startswith('.'):
            raise ValueError('Invalid manifest path')
        if (root / name).is_symlink() or sha256(root / name) != digest:
            raise ValueError('Candidate asset changed: ' + name)
    validate_reports(root, version, commit, 'distribution')
    entries = evidence.get('artifacts', {})
    if set(entries) != expected_installers(version):
        raise ValueError('Manual acceptance must cover all five installers')
    for name, entry in entries.items():
        if entry.get('sha256') != sha256(root / name):
            raise ValueError('Acceptance digest mismatch: ' + name)
        required = ['downloaded_with_browser', 'fresh_machine', 'native_ui_install', 'first_launch', 'calculation', 'no_security_bypass']
        if name.endswith('.dmg'):
            required += ['finder_launch', 'offline_launch']
        if any(entry.get(key) is not True for key in required) or not entry.get('os_version'):
            raise ValueError('Incomplete first-install evidence: ' + name)
    return manifest
