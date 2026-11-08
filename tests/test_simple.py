"""Regression tests for a non-destructive, one-click demonstration."""
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app import main
from app.models import Project, Item, Resource, Quote, utcnow
from app.store import Store
from app.simple import calculate_demo, create_router

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def client(tmp_path, monkeypatch):
    # A new app/router for each test isolates the closed-over Store and async lock.
    from fastapi import FastAPI
    local = Store(tmp_path / 'local')
    app = FastAPI()
    app.include_router(create_router(local, ROOT))
    return TestClient(app), local


def test_demo_does_not_read_or_write_real_store(client):
    c, store = client
    with patch.object(store, 'load', side_effect=AssertionError('Demo read private data')), \
         patch.object(store, 'save', side_effect=AssertionError('Demo wrote private data')), \
         patch.object(store, 'snapshot', side_effect=AssertionError('Demo read revision')):
        r = c.post('/api/simple/demo').json()
    assert r['demo'] is True
    assert r['comparison']['recommended_key'] == 'optimized'
    result = r['comparison']['strategies'][0]['result']
    assert result['cash_due_cents'] == 120200
    assert result['order_count'] == 2
    assert result['mode'] == 'model'


def test_repeated_demo_preserves_project_bytes_and_backups(client):
    c, store = client
    p = Project(name='My real data', items=[Item(id='private', title='DO NOT CHANGE')], resources=[Resource(id='real', name='Real coupon')])
    store.save(p)
    before = {str(f.relative_to(store.directory)):f.read_bytes() for f in store.directory.rglob('*') if f.is_file()}
    revision = store.snapshot()[1]
    for _ in range(3):
        assert c.post('/api/simple/demo').status_code == 200
    assert store.snapshot()[1] == revision
    assert before == {str(f.relative_to(store.directory)): f.read_bytes() for f in store.directory.rglob('*') if f.is_file()}
    assert store.load() == p
    assert len(store.list_backups()) == 0


def test_simple_plan_never_reprices_display_prices(client):
    c, store = client
    store.save(Project(items=[Item(id='a', title='Already discounted', display_price_cents=90000)]))
    r = c.post('/api/simple/plan').json()
    assert r['demo'] is False
    assert r['comparison']['recommended_key'] is None
    assert all(x['result']['cash_due_cents'] is None for x in r['comparison']['strategies'])


def test_real_plan_uses_strict_checked_amount(client):
    c, store = client
    p=Project(items=[Item(id='a',title='Test item',deposit_paid_cents=1000)])
    p.quotes=[Quote(id='q', item_ids=['a'], cash_due_cents=8800, source='manual',ledger_complete=True,
        portable_state=True,captured_at=utcnow(),context_fingerprint=p.fingerprint(),evidence_note='Test fixture')]
    store.save(p)
    r=c.post('/api/simple/plan').json()
    best=next(x['result'] for x in r['comparison']['strategies'] if x['key']==r['comparison']['recommended_key'])
    assert best['cash_due_cents']==8800  # Do NOT subtract the deposit twice.
    assert best['mode']=='strict'
    assert r['project_revision']==store.snapshot()[1]


def test_demo_is_protected_by_existing_token_middleware():
    c=TestClient(main.app)
    assert c.post('/api/simple/demo').status_code==403
    assert c.post('/api/simple/demo',headers={'x-local-token':main.TOKEN,'origin':'https://other.example'}).status_code==403
    assert c.post('/api/simple/demo',headers={'x-local-token':main.TOKEN}).status_code==200


def test_simple_and_advanced_routes_keep_token_and_csp():
    c=TestClient(main.app)
    home=c.get('/')
    assert '/static/shopping.js' in home.text
    assert main.TOKEN in home.text
    assert 'frame-ancestors' in home.headers['content-security-policy']
    advanced=c.get('/advanced')
    assert 'id="project-json"' in advanced.text and main.TOKEN in advanced.text
    assert 'advanced-entry.js' in advanced.text


def test_demo_prices_have_comparable_baselines():
    data=calculate_demo(ROOT)
    prices={r['key']:r['result']['economic_cost_cents'] for r in data['comparison']['strategies']}
    assert prices=={'optimized':120200,'single':125500,'merged':124000}
    assert prices['single']-prices['optimized']==5300


def test_changed_project_is_not_silent(client):
    c, store = client
    p=Project(items=[Item(id='a',title='a')]);store.save(p)
    from app.simple import compare_strategies
    def changing(project,opts):
        result=compare_strategies(project,opts)
        updated=project.model_copy(deep=True);updated.name='Changed';store.save(updated)
        return result
    with patch('app.simple.compare_strategies',side_effect=changing):
        result=c.post('/api/simple/plan').json()
    assert result['project_changed_during_search'] is True


def test_simple_ui_does_not_expose_model_mode_or_json():
    html=(ROOT/'app/static/index.html').read_text()
    assert 'id="solve-mode"' not in html
    assert 'id="project-json"' not in html
    assert 'id="budget"' not in html
    assert 'id="demo-first"' in html
    js=(ROOT/'app/static/simple.js').read_text()
    assert "api('simulate'" not in js
    assert "api('examples/demo'" not in js
    assert "'simple/' + (demo ? 'demo' : 'plan')" in js


def test_simple_script_passes_node_syntax_check():
    import shutil, subprocess
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is required for frontend syntax check')
    for name in ('simple.js', 'advanced-entry.js'):
        subprocess.run([node, '--check', str(ROOT/'app/static'/name)], check=True, capture_output=True, text=True)
