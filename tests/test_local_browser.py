"""Local user-controlled browser contract. No network, live account or payments."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from app.adapters.browser import BrowserAdapter
from app.cart import ConnectInput, create_cart_router, filter_rows, normalized_capture
from app.store import Store

ROOT = Path(__file__).resolve().parents[1]


class Nodes:
    def __init__(self, visible=False): self.visible=visible
    async def count(self): return int(self.visible)
    def nth(self, index): return self
    async def is_visible(self): return self.visible


class Page:
    url = 'https://cart.taobao.com/cart.htm'
    def __init__(self): self.challenge=''; self.visits=[]
    def locator(self, selector): return Nodes(selector==self.challenge)
    async def goto(self, url, **kwargs): self.url=url; self.visits.append(url)


class Context:
    def __init__(self, page): self.pages=[page]; self.callback=None; self.closed=False
    def on(self, event, callback):
        assert event=='close'; self.callback=callback
    async def close(self):
        self.closed=True
        if self.callback: self.callback()


@pytest.fixture
def browser(tmp_path,monkeypatch):
    monkeypatch.delenv('TAOBAO_PLATFORM_PERMISSION_REFERENCE',raising=False)
    page=Page(); context=Context(page)
    launch=AsyncMock(return_value=context); stop=AsyncMock()
    driver=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch),stop=stop)
    from playwright import async_api
    monkeypatch.setattr(async_api,'async_playwright',lambda:SimpleNamespace(start=AsyncMock(return_value=driver)))
    adapter=BrowserAdapter(tmp_path)
    return adapter,page,context,launch,stop


def run(coro): return asyncio.run(coro)


def test_local_confirmation_is_sufficient_without_platform_reference(browser):
    adapter,page,context,launch,_=browser
    result=run(adapter.launch(local_consent=True))
    assert result['opened'] and adapter.permitted()
    args,kwargs=launch.call_args
    assert Path(args[0]).name=='browser-profile'
    assert kwargs['headless'] is False and kwargs['chromium_sandbox'] is True
    assert kwargs['accept_downloads'] is False
    assert 'storage_state' not in kwargs
    assert page.visits==['https://cart.taobao.com/cart.htm']


def test_unconfirmed_launch_does_not_open_browser(browser):
    adapter,_,_,launch,_=browser
    with pytest.raises(ValueError,match='本人'): run(adapter.launch())
    launch.assert_not_called()


def test_old_platform_environment_does_not_enable_session(browser,monkeypatch):
    monkeypatch.setenv('TAOBAO_PLATFORM_PERMISSION_REFERENCE','ignored')
    adapter,_,_,launch,_=browser
    assert not adapter.permitted()
    with pytest.raises(ValueError): run(adapter.launch())
    launch.assert_not_called()


def test_repeated_launch_reuses_same_local_window(browser):
    adapter,_,_,launch,_=browser
    run(adapter.launch(local_consent=True));run(adapter.launch(local_consent=True))
    assert launch.await_count==1


def test_disconnect_revokes_consent_and_stops_driver(browser):
    adapter,_,context,_,stop=browser
    run(adapter.launch(local_consent=True));run(adapter.close())
    assert not adapter.permitted() and adapter.context is None
    assert context.closed and stop.await_count==1
    with pytest.raises(ValueError):run(adapter.page())


def test_manually_closed_window_requires_reconnect(browser):
    adapter,_,context,_,_=browser
    run(adapter.launch(local_consent=True));context.callback()
    assert not adapter.permitted() and adapter.context is None
    with pytest.raises(ValueError):run(adapter.page())


def test_failed_launch_does_not_leave_session_enabled(browser):
    adapter,_,_,launch,stop=browser;launch.side_effect=RuntimeError('unavailable')
    with pytest.raises(ValueError,match='Chrome'):run(adapter.launch(local_consent=True))
    assert not adapter.permitted() and adapter.context is None
    assert stop.await_count==1


@pytest.mark.parametrize('selector',['#baxia-dialog-content','#nc_1_wrapper','[data-testid="captcha"]','iframe[src*="captcha"]','iframe[src*="baxia"]'])
def test_visible_challenge_stops_page_operations(browser,selector):
    adapter,page,_,_,_=browser
    run(adapter.launch(local_consent=True));page.challenge=selector
    with pytest.raises(ValueError,match='安全验证'):run(adapter.page())
    page.challenge=''
    assert run(adapter.page()) is page


@pytest.mark.parametrize('url',['https://login.taobao.com/member/login.jhtml','https://passport.tmall.com/login'])
def test_login_requires_manual_completion(browser,url):
    adapter,page,_,_,_=browser;run(adapter.launch(local_consent=True));page.url=url
    with pytest.raises(ValueError,match='扫码'):run(adapter.page())


@pytest.mark.parametrize('url',['https://evil.example/','https://taobao.com.evil.example/','http://cart.taobao.com/'])
def test_foreign_page_is_not_automated(browser,url):
    adapter,page,_,_,_=browser;run(adapter.launch(local_consent=True));page.url=url
    with pytest.raises(ValueError):run(adapter.page())


@pytest.fixture
def client(browser,tmp_path):
    adapter=browser[0];store=Store(tmp_path/'project')
    app=FastAPI();app.include_router(create_cart_router(store,adapter,ROOT))
    @app.exception_handler(ValueError)
    async def error(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)
    with TestClient(app) as c:yield c


def test_cart_connect_has_no_platform_fields(client,browser):
    assert set(ConnectInput.model_fields)=={'local_consent','new_account'}
    assert client.post('/api/cart/connect',json={'local_consent':True}).status_code==200
    result=client.get('/api/cart/status').json()
    assert result['mode']=='local_browser' and result['official_api_required'] is False
    assert 'permission_configured' not in result
    assert client.post('/api/cart/disconnect').status_code==200


def test_cart_rejects_missing_confirmation(client,browser):
    assert client.post('/api/cart/connect',json={}).status_code==400
    browser[3].assert_not_called()


def test_account_switch_requires_disconnect(client):
    assert client.post('/api/cart/connect',json={'local_consent':True}).status_code==200
    assert client.post('/api/cart/connect',json={'local_consent':True,'new_account':True}).status_code==400


def test_status_explains_manual_challenge(client,browser):
    client.post('/api/cart/connect',json={'local_consent':True})
    browser[1].challenge='#nc_1_wrapper'
    result=client.get('/api/cart/status').json()
    assert result['state']=='needs_attention' and '安全验证' in result['note']
    assert result['snapshot'] is None


def test_closed_connection_cannot_import_old_snapshot(client):
    r=client.post('/api/cart/import',json={'snapshot_id':'old','line_ids':['a'],'account_confirmed':True})
    assert r.status_code==400


def test_group_missing_never_falls_back_to_all():
    snap=normalized_capture({'items':[]},'test')
    with pytest.raises(ValueError,match='未回退'):filter_rows(snap,'taobao:missing')


def test_ui_no_longer_requests_platform_reference():
    html=(ROOT/'app/static/shopping.html').read_text('utf-8')
    js=(ROOT/'app/static/shopping.js').read_text('utf-8')
    assert 'id="permission"' not in html and 'id="attested"' not in html
    assert 'permission_reference' not in js and 'permission_attested' not in js
    assert 'window.confirm' in js and 'local_consent:true' in js
