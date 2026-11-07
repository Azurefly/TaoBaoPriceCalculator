"""Local-only UI smoke check; does not open or sign in to Taobao."""
from pathlib import Path
import json
import os
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['PROMOPATH_DATA_DIR']=tempfile.mkdtemp(prefix='promopath-ui-')
from fastapi.testclient import TestClient
from app.main import app,TOKEN
from playwright.sync_api import sync_playwright

url=os.getenv('PROMOPATH_TEST_URL','http://127.0.0.1:8766')
out=Path(os.getenv('PROMOPATH_TEST_ARTIFACTS',str(Path(__file__).resolve().parents[1]/'test-artifacts')))
out.mkdir(parents=True,exist_ok=True)
errors=[]
client=TestClient(app)
root=Path(__file__).resolve().parents[1]
def bridge(source,url,opts):
    r=client.request(opts.get('method','GET'),url,headers=opts.get('headers',{}),content=opts.get('body'))
    return {'status':r.status_code,'body':r.text}
with sync_playwright() as p:
    executable=os.getenv('PROMOPATH_TEST_CHROMIUM')
    browser=p.chromium.launch(headless=True, **({'executable_path':executable} if executable else {}),args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',lambda d:d.accept())
    # The sandbox's Chromium policy blocks localhost navigation. Test the real UI
    # in memory with a TestClient transport; do not disable administrator policies.
    html=(root/'app/static/index.html').read_text().replace('__LOCAL_TOKEN__',TOKEN)
    html=html.replace('<link rel="stylesheet" href="/static/style.css">','').replace('<script defer src="/static/app.js"></script>','')
    page.set_content(html)
    page.add_style_tag(content=(root/'app/static/style.css').read_text())
    page.expose_binding('apiBridge',bridge)
    page.evaluate("window.fetch=async(url,opts={})=>{const r=await window.apiBridge(String(url),opts);return new Response(r.body,{status:r.status,headers:{'Content-Type':'application/json'}})}")
    page.add_script_tag(content=(root/'app/static/app.js').read_text())
    page.wait_for_function("document.querySelector('#stats').children.length===4")
    page.locator('#load-demo').click()
    page.get_by_role('heading',name='通用促销 · 合成演示（非淘宝真实报价）').wait_for()
    page.screenshot(path=str(out/'overview.png'),full_page=True)
    page.locator('[data-tab="quotes"]').click()
    page.locator('#simulate').click()
    page.wait_for_function("document.querySelector('#simulation-result').textContent.includes('quote_count')")
    page.locator('[data-tab="plan"]').click()
    page.locator('#solve-mode').select_option('model')
    page.locator('#step-seconds').fill('0')
    page.locator('#optimize').click()
    page.get_by_role('heading',name='已输入有效报价集合内最优').wait_for()
    result=page.locator('#plan-result details pre').text_content()
    (out/'demo-result.json').write_text(result,encoding='utf-8')
    page.screenshot(path=str(out/'plan.png'),full_page=True)
    page.locator('#solve-mode').select_option('strict')
    page.locator('#optimize').click()
    page.get_by_role('heading',name='当前证据不足，无法形成完整付款计划').wait_for()
    page.locator('#load-history').click()
    page.get_by_role('heading',name='历史订单复盘 · 缺少结算证据，不输出最优金额').wait_for()
    page.locator('[data-tab="quotes"]').click()
    page.locator('#simulate').click()
    page.wait_for_function("document.querySelector('#simulation-result').textContent.includes('缺少可靠')")
    page.locator('[data-tab="overview"]').click()
    page.screenshot(path=str(out/'historical.png'),full_page=True)
    page.locator('#new-item').click()
    page.locator('#f-title').fill('页面录入测试')
    page.locator('#f-display').fill('88.80')
    page.locator('#f-base').fill('100')
    page.locator('#f-evidence').fill('本地UI测试')
    page.locator('#f-deposit').fill('0')
    page.locator('#f-depositConfirmed').check()
    page.locator('#edit-form button[type="submit"]').click()
    page.locator('#editor').wait_for(state='hidden')
    page.wait_for_function("document.querySelector('#items').textContent.includes('页面录入测试')")
    page.locator('[data-tab="data"]').click()
    page.locator('#refund-scenario').click()
    page.wait_for_function("document.querySelector('#refund-result').textContent.includes('810.00')")
    page.locator('[data-tab="browser"]').click()
    page.locator('#launch-browser').click()
    page.wait_for_function("document.querySelector('#message').textContent.includes('默认关闭')")
    page.screenshot(path=str(out/'browser.png'),full_page=True)
    # v0.2: exercise the newly added real front-end flows.
    page.locator('#load-demo').click()
    page.get_by_role('heading',name='通用促销 · 合成演示（非淘宝真实报价）').wait_for()
    page.locator('[data-tab="resources"]').click()
    page.locator('#new-rule').click()
    page.locator('#f-name').fill('UI折扣规则草稿')
    page.locator('#f-rule-kind').select_option('percent')
    page.locator('#f-fold').fill('9')
    page.locator('#f-cap').fill('50')
    page.locator('#f-source-note').fill('本地合成规则，并非淘宝活动')
    page.locator('#edit-form button[type="submit"]').click()
    page.locator('#editor').wait_for(state='hidden')
    page.wait_for_function("document.querySelector('#rules-list').textContent.includes('UI折扣规则草稿')")
    data=client.get('/api/project',headers={'x-local-token':TOKEN}).json()['project']
    created=next(r for r in data['rules'] if r['name']=='UI折扣规则草稿')
    assert created['discount_bps']==1000 and created['cap_cents']==5000 and not created['confirmed']

    page.locator('[data-tab="quotes"]').click()
    page.locator('#simulate').click()
    page.wait_for_function("document.querySelector('#simulation-result').textContent.includes('quote_count')")
    page.locator('[data-edit="quote"]').first.click()
    page.locator('#edit-form button[type="submit"]').click()
    page.wait_for_function("document.querySelector('#editor-error').textContent.includes('模拟金额')")
    page.locator('#close-editor').click()
    page.locator('[data-tab="plan"]').click()
    page.locator('#solve-mode').select_option('model')
    page.locator('#step-seconds').fill('0')
    page.locator('#compare').click()
    page.locator('#comparison-result .stat').nth(2).wait_for()
    assert page.locator('#comparison-result').inner_text().count('逐件付款')==1
    assert '1202.00' in page.locator('#comparison-result').inner_text()
    page.screenshot(path=str(out/'comparison.png'),full_page=True)
    page.locator('#optimize').click()
    page.get_by_role('heading',name='已输入有效报价集合内最优').wait_for()
    with page.expect_download() as download_info:
        page.locator('#export-plan').click()
    download_info.value.save_as(str(out/'exported-plan.md'))
    assert '1202.00' in (out/'exported-plan.md').read_text()
    page.screenshot(path=str(out/'optimized-comparison.png'),full_page=True)

    # Fully synthetic checked-quote fixtures, deliberately NOT a Taobao account test.
    from datetime import timedelta
    from app.models import Project, Item, Resource, Quote, utcnow
    now=utcnow()
    project=Project(name='UI 合成记账验收（非淘宝真实交易）',
                    items=[Item(id='a',title='验收商品A'),Item(id='b',title='验收商品B')],
                    resources=[Resource(id='earned',name='演示返券',available=0),Resource(id='unused',name='需归零',available=0)])
    project.quotes=[Quote(id='a',item_ids=['a'],cash_due_cents=1000,extra_cost_cents=800,
            granted={'earned':1},requires_zero=['unused'],source='manual',ledger_complete=True,portable_state=True,
            context_fingerprint=project.fingerprint(),captured_at=now,valid_until=now+timedelta(minutes=5),evidence_note='测试夹具'),
            Quote(id='b',item_ids=['b'],cash_due_cents=500,consumed={'earned':1},requires_done=['a'],
            source='manual',ledger_complete=True,portable_state=True,context_fingerprint=project.fingerprint(),
            captured_at=now,valid_until=now+timedelta(minutes=5),evidence_note='测试夹具')]
    d=client.get('/api/project',headers={'x-local-token':TOKEN}).json()
    assert client.put('/api/project',headers={'x-local-token':TOKEN,'x-project-revision':d['revision']},json=project.model_dump(mode='json')).status_code==200
    page.evaluate('refresh()')
    page.locator('[data-tab="quotes"]').click()
    page.locator('[data-edit="quote"][data-id="a"]').click()
    page.locator('#f-note').fill('修改说明但不刷新核价时间')
    page.locator('#edit-form button[type="submit"]').click()
    page.locator('#editor').wait_for(state='hidden')
    current=client.get('/api/project',headers={'x-local-token':TOKEN}).json()['project']
    updated=next(q for q in current['quotes'] if q['id']=='a')
    original=project.quotes[0].model_dump(mode='json')
    for key in ('extra_cost_cents','granted','requires_zero','captured_at','valid_until'):
        assert updated[key]==original[key],(key,updated[key],original[key])
    page.locator('[data-tab="plan"]').click()
    page.locator('#solve-mode').select_option('strict')
    page.locator('#optimize').click()
    page.get_by_role('heading',name='已输入有效报价集合内最优').wait_for()
    page.locator('[data-edit="payment"][data-id="a"]').click()
    page.locator('#f-grant-earned').fill('1')
    page.locator('#f-note').fill('本地自动化测试记账，并非真实付款')
    page.locator('#f-paid-confirmed').check()
    page.locator('#edit-form button[type="submit"]').click()
    page.locator('#editor').wait_for(state='hidden')
    page.wait_for_function("document.querySelector('#diagnostics').textContent.includes('已付 1')")
    current=client.get('/api/project',headers={'x-local-token':TOKEN}).json()['project']
    assert len(current['payments'])==1 and current['resources'][0]['available']==1
    page.locator('[data-tab="data"]').click()
    page.screenshot(path=str(out/'payment-ledger.png'),full_page=True)
    page.locator('#refresh-backups').click()
    page.wait_for_function("document.querySelector('#backup-list').value.length>0")
    page.locator('#restore-backup').click()
    page.wait_for_function("document.querySelector('#message').textContent.includes('已恢复备份')")
    current=client.get('/api/project',headers={'x-local-token':TOKEN}).json()['project']
    assert not current['payments'] and current['resources'][0]['available']==0
    page.set_viewport_size({'width':390,'height':844})
    page.locator('[data-tab="overview"]').click()
    page.screenshot(path=str(out/'mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
    browser.close()
report={'ui_checks':'passed','transport':'in-memory Chromium UI + FastAPI TestClient bridge; localhost navigation blocked by sandbox administrator policy','page_errors':errors,'tested':['加载合成演示','模拟报价','求解与路径展示','严格模式拒绝模拟价格','历史数据拒绝重复减券','表单新增商品','退款情景现金核算','默认拒绝未配置许可的浏览器','可视化折扣规则表单','模拟报价不能未经核价升级为实价','三种付款策略比较','导出付款清单','编辑保留费用返券前置条件及观察时间','实际付款记账及权益余额更新','页面备份恢复','390px窄屏布局'], 'not_tested':['淘宝真实账号登录','真实购物车DOM','真实结算/活动互斥','真实支付或退款']}
(out/'ui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
assert not errors
