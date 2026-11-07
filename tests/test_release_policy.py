"""New publication gates, tested without inventing OS signing/first-launch evidence."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_policy import evaluate, channel, expected_installers, sha256, validate_reports, validate_acceptance
from build_release_manifest import build

VERSION = '0.3.2'
COMMIT = 'a' * 40
COMMON = ['installed_runtime', 'artifact_integrity']
CHECKS = {
    'darwin': COMMON + ['signature_integrity', 'signature_after_runtime', 'embedded_native_signatures', 'architecture',
                       'developer_id', 'expected_team', 'app_staple', 'dmg_staple', 'gatekeeper', 'distribution_policy', 'dmg_policy'],
    'win32': COMMON + ['uninstall_completed', 'install_path_with_spaces', 'authenticode_installer',
                      'authenticode_main', 'authenticode_backend', 'same_publisher'],
    'linux': COMMON + ['deb_system_install', 'deb_uninstall', 'appimage_fuse_mount', 'appimage_direct_runtime'],
}

def report(platform='darwin', arch='arm64', selected='distribution'):
    return {'schema_version': 2, 'source_commit': COMMIT, 'version': VERSION, 'platform': platform,
            'architecture': arch, 'channel': selected, 'checks': {name: True for name in CHECKS[platform]}}


def fixture(root, selected='distribution'):
    root.mkdir(exist_ok=True)
    for name in expected_installers(VERSION):
        (root / name).write_bytes(('synthetic unit-test fixture: ' + name).encode())
    (root / 'package-lock.json').write_text('{}')
    reports = []
    for plat, arch in [('darwin', 'arm64'), ('darwin', 'x64'), ('win32', 'x64'), ('linux', 'x64')]:
        r = report(plat, arch, selected)
        names = {name for name in expected_installers(VERSION) if
                 (plat == 'darwin' and name.endswith(f'mac-{arch}.dmg')) or
                 (plat == 'win32' and name.endswith('.exe')) or
                 (plat == 'linux' and name.endswith(('.deb', '.AppImage')))}
        r['artifact_sha256'] = {name: sha256(root / name) for name in names}
        r.update(evaluate(r))
        p = root / f'installer-validation-{plat}-{arch}.json'
        p.write_text(json.dumps(r)); reports.append(p)
    return reports


def evidence(root):
    m = json.loads((root / 'build-manifest.json').read_text())
    return {'schema_version': 1, 'tag': 'v' + VERSION, 'source_commit': COMMIT, 'reviewer': 'unit-test reviewer',
            'tested_at': datetime.now(timezone.utc).isoformat(),
            'artifacts': {name: {'sha256': sha256(root / name), 'os_version': 'synthetic test OS',
                **{key: True for key in ['downloaded_with_browser', 'fresh_machine', 'native_ui_install',
                    'first_launch', 'calculation', 'finder_launch', 'offline_launch', 'no_security_bypass']}}
                for name in expected_installers(VERSION)}}


def test_unknown_channel_rejected():
    with pytest.raises(ValueError): channel('release')


def test_gatekeeper_failure_cannot_be_hidden_by_passed_flag():
    r = report(); r['checks']['gatekeeper'] = False; r['passed'] = True
    assert evaluate(r)['preview_checks_passed']
    assert not evaluate(r)['distribution_checks_passed']

@pytest.mark.parametrize('key', CHECKS['darwin'])
def test_each_required_mac_check_is_fail_closed(key):
    r = report(); del r['checks'][key]
    assert not evaluate(r)['distribution_checks_passed']

@pytest.mark.parametrize('value', ['true', 1, None, False])
def test_check_values_must_be_exact_boolean(value):
    r = report(); r['checks']['gatekeeper'] = value
    assert not evaluate(r)['distribution_checks_passed']

@pytest.mark.parametrize('key', ['deb_system_install', 'deb_uninstall', 'appimage_fuse_mount', 'appimage_direct_runtime'])
def test_linux_extraction_does_not_pass_actual_installation(key):
    r = report('linux', 'x64'); r['checks'][key] = False
    assert not evaluate(r)['preview_checks_passed']

@pytest.mark.parametrize('key', ['authenticode_installer', 'authenticode_backend', 'same_publisher'])
def test_windows_signature_requirements(key):
    r = report('win32', 'x64'); r['checks'][key] = False
    assert not evaluate(r)['distribution_checks_passed']


def test_valid_set_and_manifest(tmp_path):
    fixture(tmp_path)
    m, notes = build(tmp_path, VERSION, COMMIT, 'distribution')
    assert len(m['targets']) == 4 and not m['first_launch_tested']
    assert '草稿' in notes
    assert validate_acceptance(tmp_path, evidence(tmp_path))['source_commit'] == COMMIT


def test_changed_installer_cannot_reuse_validation(tmp_path):
    fixture(tmp_path)
    (tmp_path / next(iter(expected_installers(VERSION)))).write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'): validate_reports(tmp_path, VERSION, COMMIT, 'distribution')


def test_reports_must_cover_all_targets(tmp_path):
    files = fixture(tmp_path); files[0].unlink()
    with pytest.raises(ValueError): validate_reports(tmp_path, VERSION, COMMIT, 'distribution')


def test_stale_commit_rejected(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError): validate_reports(tmp_path, VERSION, 'b' * 40, 'distribution')


def test_preview_cannot_be_promoted(tmp_path):
    fixture(tmp_path, 'preview'); build(tmp_path, VERSION, COMMIT, 'preview')
    with pytest.raises(ValueError): validate_acceptance(tmp_path, evidence(tmp_path))

@pytest.mark.parametrize('key', ['sha256', 'downloaded_with_browser', 'fresh_machine', 'native_ui_install',
                                'first_launch', 'calculation', 'finder_launch', 'offline_launch', 'no_security_bypass', 'os_version'])
def test_manual_acceptance_missing_field_rejected(tmp_path, key):
    fixture(tmp_path); build(tmp_path, VERSION, COMMIT, 'distribution'); e = evidence(tmp_path)
    mac = next(name for name in e['artifacts'] if name.endswith('mac-arm64.dmg'))
    e['artifacts'][mac].pop(key)
    with pytest.raises(ValueError): validate_acceptance(tmp_path, e)


def test_empty_manual_record_cannot_pass(tmp_path):
    fixture(tmp_path); build(tmp_path, VERSION, COMMIT, 'distribution')
    with pytest.raises(ValueError): validate_acceptance(tmp_path, {})


def test_no_timestamp_from_before_build(tmp_path):
    fixture(tmp_path); build(tmp_path, VERSION, COMMIT, 'distribution'); e = evidence(tmp_path)
    e['tested_at'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError): validate_acceptance(tmp_path, e)


def test_manifest_never_uploads_unexpected_credentials(tmp_path):
    fixture(tmp_path); (tmp_path / 'certificate.p12').write_bytes(b'fixture')
    with pytest.raises(ValueError): build(tmp_path, VERSION, COMMIT, 'distribution')


def test_reports_are_recomputed_not_trusted(tmp_path):
    files = fixture(tmp_path)
    r = json.loads(files[0].read_text()); r['checks']['gatekeeper'] = False; r['distribution_checks_passed'] = True
    files[0].write_text(json.dumps(r))
    with pytest.raises(ValueError): validate_reports(tmp_path, VERSION, COMMIT, 'distribution')


def test_receipt_cannot_reference_other_target_installer(tmp_path):
    files = fixture(tmp_path)
    r = json.loads(files[0].read_text()); name = next(n for n in expected_installers(VERSION) if n.endswith('.exe'))
    r['artifact_sha256'] = {name: sha256(tmp_path / name)}; files[0].write_text(json.dumps(r))
    with pytest.raises(ValueError): validate_reports(tmp_path, VERSION, COMMIT, 'distribution')


def test_preview_notes_recompute_trust_instead_of_echoing_claim(tmp_path):
    files = fixture(tmp_path, 'preview')
    r = json.loads(files[0].read_text()); r['checks']['gatekeeper'] = False; r['distribution_checks_passed'] = True
    files[0].write_text(json.dumps(r))
    reports = validate_reports(tmp_path, VERSION, COMMIT, 'preview')
    assert not next(x for x in reports if x['platform'] == 'darwin' and x['architecture'] == 'arm64')['distribution_checks_passed']


def test_runtime_error_cannot_pass_with_true_checks(tmp_path):
    files = fixture(tmp_path)
    r = json.loads(files[0].read_text()); r['error'] = 'cleanup failed'; files[0].write_text(json.dumps(r))
    with pytest.raises(ValueError): validate_reports(tmp_path, VERSION, COMMIT, 'distribution')
