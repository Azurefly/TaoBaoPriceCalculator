"""Real Chromium UI + TestClient bridge, no Taobao/network/payment access."""
from pathlib import Path
import os, sys, json, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['PROMOPATH_DATA_DIR'] = tempfile.mkdtemp(prefix='tbpc-simple-ui-')
from fastapi.testclient import TestClient
from app.main import app, TOKEN, store
from app.models import Project, Item, Resource, Quote, utcnow
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
out=Path(os.getenv('PROMOPATH_TEST_ARTIFACTS',str(root/'test-artifacts/simple')))
out.mkdir(parents=True,exist_ok=True)
client=TestClient(app)
errors=[]; requests=[]; checks=[]
def bridge(source,url,opts):
    requests.append((opts.get('method','GET'),url))
    r=client.request(opts.get('method','GET'),url,headers=opts.get('headers',{}),content=opts.get('body'))
    return {'status':r.status_code,'body':r.text}
def mount(page):
    html=(root/'app/static/index.html').read_text().replace('__LOCAL_TOKEN__',TOKEN)
    html=html.replace('<link rel="stylesheet" href="/static/simple.css">','').replace('<script defer src="/static/simple.js"></script>','')
    page.set_content(html)
    page.add_style_tag(content=(root/'app/static/simple.css').read_text())
    page.expose_binding('apiBridge',bridge)
    page.evaluate("window.fetch=async(url,opts={})=>{const r=await window.apiBridge(String(url),opts);return new Response(r.body,{status:r.status,headers:{'Content-Type':'application/json'}})}")
    page.add_script_tag(content=(root/'app/static/simple.js').read_text())
    page.wait_for_function("document.body.dataset.ready==='true'")
def capture(page,name):page.screenshot(path=str(out/name),full_page=True)
with sync_playwright() as p:
    binary=os.getenv('PROMOPATH_TEST_CHROMIUM')
    browser=p.chromium.launch(headless=True,**({'executable_path':binary} if binary else {}))
    page=browser.new_page(viewport={'width':1365,'height':1000},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)))
    mount(page)
    assert page.locator('#empty').is_visible()
    assert page.locator('#next').inner_text()=='＋ 添加商品'
    assert not page.locator('#screen-prices').is_visible()
    capture(page,'01-home.png');checks.append('首页只有清单入口和一键体验，不暴露参数')
    before=store.snapshot()[1];start=len(requests)
    page.locator('#demo-first').click()
    page.locator('.result-money').wait_for()
    assert '1,202' not in page.locator('.result-money').inner_text()
    assert page.locator('.result-money').inner_text()=='¥1202.00'
    assert '¥53.00' in page.locator('.save-line').inner_text()
    assert store.snapshot()[1]==before
    assert [r for r in requests[start:] if r[0]!='GET']==[('POST','/api/simple/demo')]
    assert page.locator('#demo-banner').is_visible()
    capture(page,'02-demo-result.png');checks.append('一次点击加载数据并完成计算，结果1202元，明确示例')
    page.locator('[data-demo-step="0"]').click()
    page.locator('[data-demo-step="1"]').click()
    assert page.get_by_role('heading',name='体验完成，就是这么简单').is_visible()
    assert store.snapshot()[1]==before
    checks.append('两步模拟操作不创建真实付款，不写项目')
    page.locator('#exit-demo').click()
    page.locator('#empty').wait_for()
    checks.append('退出体验回到原清单')
    page.locator('#add-first').click()
    page.locator('#f-title').fill('面霜 50ml')
    page.locator('#f-price').fill('99.50')
    page.locator('#save').click();page.locator('#editor').wait_for(state='hidden')
    assert len(store.load().items)==1
    assert store.load().items[0].pricing_base_cents is None
    checks.append('只填名称和可选展示价即可添加，不将展示价当基数')
    page.locator('#add-item').click();page.locator('#f-title').fill('猫粮 6kg');page.locator('#save').click();page.locator('#editor').wait_for(state='hidden')
    before=store.snapshot()[1]; files_before={str(f):f.read_bytes() for f in store.directory.rglob('*') if f.is_file()}
    page.locator('#demo').click();page.locator('.result-money').wait_for();page.locator('#exit-demo').click();page.locator('#shopping').wait_for()
    assert store.snapshot()[1]==before
    assert files_before=={str(f):f.read_bytes() for f in store.directory.rglob('*') if f.is_file()}
    assert '面霜' in page.locator('#items').inner_text()
    checks.append('有真实清单时体验也不改项目、备份或优惠余额')
    page.locator('#next').click()
    page.locator('#add-quote').click();page.locator('#f-amount').fill('180')
    page.locator('#save').click()
    assert page.locator('#editor').is_visible()
    assert '勾选' in page.locator('#form-error').inner_text()
    page.locator('#f-confirmed').check();page.locator('#save').click();page.locator('#editor').wait_for(state='hidden')
    assert len(store.load().quotes)==1
    assert store.load().quotes[0].cash_due_cents==18000
    assert not store.load().quotes[0].portable_state
    checks.append('真实价格需确认，不默认多笔优惠互不影响')
    capture(page,'03-real-prices.png')
    page.locator('#next').click();page.locator('.result-money').wait_for()
    assert page.locator('.result-money').inner_text()=='¥180.00'
    assert '不虚报' in page.locator('#result').inner_text()
    capture(page,'04-real-result.png');checks.append('一键比较真实金额，未知逐件对照不虚报省钱')
    page.locator('[data-paid="0"]').click();page.locator('#save').click()
    assert not store.load().payments
    page.locator('#f-paid').check();page.locator('#save').click();page.locator('#editor').wait_for(state='hidden')
    assert len(store.load().payments)==1
    assert len(store.load().paid_item_ids())==2
    checks.append('明确确认后才记录付款；不会自动付款')
    # Fresh page for XSS and small-screen regressions; malicious title must remain text.
    project=Project(items=[Item(id='unsafe',title='<img src=x onerror="alert(1)">')])
    store.save(project)
    page.close();page=browser.new_page(viewport={'width':390,'height':844})
    page.on('pageerror',lambda e:errors.append(str(e)));mount(page)
    assert page.locator('#items img').count()==0
    assert '<img' in page.locator('#items').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
    checks.append('商品名称安全转义；390px无横向溢出')
    page.locator('#demo').click();page.locator('.result-money').wait_for()
    capture(page,'05-mobile-demo.png')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
    checks.append('窄屏一键体验和付款步骤可用')
    page.locator('#exit-demo').click();page.locator('#shopping').wait_for();page.locator('#next').click()
    page.locator('[data-stage="result"]').click()
    assert page.get_by_role('button',name='去核对价格').is_visible()
    checks.append('缺少价格时给出可点击下一步，不显示技术报错')
    browser.close()
report={'checks_passed':len(checks),'checks':checks,'page_errors':errors,'transport':'Chromium in-memory page + real FastAPI TestClient bridge',
    'not_tested':['真实淘宝登录或采价','原生安装器','真实用户的首次使用体验']}
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
assert not errors
