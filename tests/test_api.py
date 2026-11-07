import os
import tempfile
os.environ['PROMOPATH_DATA_DIR']=tempfile.mkdtemp(prefix='promopath-test-')
from fastapi.testclient import TestClient
from app.main import app,TOKEN

client=TestClient(app)
headers={'x-local-token':TOKEN}

def test_home_and_auth():
    assert client.get('/').status_code==200
    assert client.get('/api/project').status_code==403
    assert client.get('/api/project',headers=headers).status_code==200
    assert client.get('/api/project',headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert client.get('/',headers={'Host':'evil.example'}).status_code==400

def test_demo_end_to_end():
    p=client.get('/api/examples/demo',headers=headers).json()
    h = {**headers, 'x-project-revision':client.get('/api/project',headers=headers).json()['revision']}
    assert client.put('/api/project',headers=h,json=p).status_code==200
    r=client.post('/api/simulate',headers=headers,json={'budget':100})
    assert r.status_code==200 and r.json()['quote_count']>0
    r=client.post('/api/optimize',headers=headers,json={'mode':'model','step_seconds':0})
    assert r.status_code==200 and r.json()['cash_due_cents']>0
    strict=client.post('/api/optimize',headers=headers,json={'mode':'strict'})
    assert strict.json()['status']=='no_feasible_plan'

def test_no_order_or_payment_endpoints():
    for path in ['pay','order','submit','refund']:
        assert client.post('/api/'+path,headers=headers,json={}).status_code==404

def test_validation_and_parse():
    assert client.put('/api/project',headers=headers,json={'oops':'x'}).status_code==422
    r=client.post('/api/parse-coupons',headers=headers,json={'text':'满1000减125，满200减25'})
    assert len(r.json()['drafts'])==2
    assert r.json()['drafts'][0]['confirmed'] is False

def test_browser_denied_without_permission(monkeypatch):
    monkeypatch.delenv('TAOBAO_PLATFORM_PERMISSION_REFERENCE',raising=False)
    r=client.post('/api/browser/launch',headers=headers,json={})
    assert r.status_code==400
