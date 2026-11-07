"""Regression cases from real desktop distribution validation."""
import json
from pathlib import Path
import sys
import pytest
from app.models import Project
from app.store import Store

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_installers import nsis_command


def backup_names(store):
    return [json.loads((store.backups / row['name']).read_text())['name']
            for row in store.list_backups()]


def test_backups_survive_frozen_clock_and_keep_last_twenty(tmp_path, monkeypatch):
    monkeypatch.setattr('app.store.time.time_ns', lambda: 1000)
    store = Store(tmp_path)
    for i in range(26):
        store.save(Project(name=str(i)))
    assert len(store.list_backups()) == 20
    assert backup_names(store) == [str(i) for i in range(24, 4, -1)]
    assert store.load().name == '25'


def test_backup_counter_survives_restart_and_clock_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr('app.store.time.time_ns', lambda: 9999)
    store = Store(tmp_path)
    store.save(Project(name='first'))
    store.save(Project(name='second'))
    monkeypatch.setattr('app.store.time.time_ns', lambda: 9)
    restarted = Store(tmp_path)
    restarted.save(Project(name='third'))
    assert backup_names(restarted) == ['second', 'first']
    assert restarted.list_backups()[0]['name'] == 'project-10000.json'
    restarted.restore(restarted.list_backups()[1]['name'])
    assert restarted.load().name == 'first'


def test_backup_rotation_does_not_delete_unrelated_names(tmp_path, monkeypatch):
    monkeypatch.setattr('app.store.time.time_ns', lambda: 1000)
    store = Store(tmp_path)
    store.backups.mkdir()
    unrelated = store.backups / 'project-not-a-backup.json'
    unrelated.write_text('leave this alone')
    for i in range(24):
        store.save(Project(name=str(i)))
    assert len(store.list_backups()) == 20
    assert unrelated.read_text() == 'leave this alone'


@pytest.mark.parametrize('uninstall,option', [(False, '/D='), (True, '_?=')])
def test_nsis_special_directory_is_last_and_unquoted(uninstall, option):
    executable, target = Path('C:/temp dir/check.exe'), Path('C:/installed with spaces')
    command = nsis_command(executable, target, uninstall)
    assert command.startswith(f'"{executable}" /S ')
    assert command.endswith(option + str(target))
    assert '"' not in command.split(option, 1)[1]


@pytest.mark.parametrize('bad', ['bad"dir', 'bad\ndir', 'bad\x00dir'])
def test_nsis_rejects_invalid_directory(bad):
    with pytest.raises(ValueError):
        nsis_command(Path('setup.exe'), Path(bad))
