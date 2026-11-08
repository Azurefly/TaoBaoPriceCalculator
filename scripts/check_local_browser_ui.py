"""Chromium UI tests with deterministic HTTP fixtures, never accessing Taobao."""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
errors=[];calls=[];checks=[]
snap={'snapshot_id':'s1','items':[{'line_id':'1','title':'示例面霜','quantity':1,'can_import':True,'shop_id':'shop1','shop_name':'护肤店','display_price_cents':12300,'group_ids':['beauty']}], 'groups':[{'id':'view:current','name':'当前页面','source':'view'},{'id':'taobao:beauty','native_id':'beauty','name':'护肤','source':'taobao','active':True},{'id':'taobao:pet','native_id':'pet','name':'宠物','source':'taobao','active':False}], 'errors':[],'warnings':[],'active_group_id':'beauty'}

def bridge(path, options):
    body=options.get('body');calls.append((path,body));data={};status=200
    if path=='/api/project':data={'project':{'items':[],'rules':[],'resources':[]},'revision':'rev'}
    elif path=='/api/cart/connect':
        assert json.loads(body)=={'local_consent':True,'new_account':False}
        data={'opened':True}
    elif path=='/api/cart/read':
        payload=json.loads(body or '{}')
        if payload.get('native_group_id')=='pet':status=400;data={'detail':'分组尚未更新，请在浏览器确认后重新读取'}
        else:data=snap
    elif path=='/api/cart/disconnect':data={'closed':True,'note':'已停止读取'}
    elif path=='/api/shopping/demo':data={'demo':True,'cart':snap,'project':{'items':[],'rules':[]},'comparison':{'strategies':[]}}
    elif path=='/api/cart/open':data={'opened':True}
    else:status=404;data={'detail':'Unexpected test route'}
    return {'status':status,'data':data}

with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True)
    page=browser.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    html=(ROOT/'app/static/shopping.html').read_text().replace('__LOCAL_TOKEN__','test')
    html=re.sub(r'<script[^>]*>.*?</script>|<link[^>]*>', '', html)
    page.set_content(html)
    page.expose_function('__fixture',bridge)
    page.evaluate("window.fetch=async (url,options={})=>{const r=await window.__fixture(String(url),options);return {ok:r.status===200,json:async()=>r.data}}")
    page.add_script_tag(content=(ROOT/'app/static/shopping.js').read_text())
    page.wait_for_selector('#loading',state='hidden')
    assert page.locator('#permission,#attested').count()==0
    checks.append('默认连接页无官方授权码或许可字段')
    def cancel(dialog):dialog.dismiss()
    page.on('dialog',cancel);page.click('#connect');page.wait_for_selector('#loading',state='hidden')
    assert not any(p=='/api/cart/connect' for p,b in calls)
    checks.append('取消首次本机确认时不发起连接')
    page.remove_listener('dialog',cancel)
    def accept(dialog):dialog.accept()
    page.on('dialog',accept);page.click('#connect');page.wait_for_selector('#loading',state='hidden')
    assert '扫码登录' in page.locator('#connection-note').inner_text()
    assert json.loads([b for p,b in calls if p=='/api/cart/connect'][-1])=={'local_consent':True,'new_account':False}
    checks.append('一次本机确认即可连接，无平台授权参数')
    page.click('#read-cart');page.wait_for_selector('#loading',state='hidden')
    assert '示例面霜' in page.locator('#cart-items').inner_text()
    page.select_option('#cart-group','taobao:beauty');page.wait_for_selector('#loading',state='hidden')
    assert json.loads([b for p,b in calls if p=='/api/cart/read'][-1])=={'native_group_id':'beauty'}
    checks.append('指定淘宝分组请求携带准确分组ID')
    page.click('#select-cart');assert page.locator('#import-cart').is_enabled()
    page.select_option('#cart-group','taobao:pet');page.wait_for_selector('#loading',state='hidden')
    assert page.locator('#import-cart').is_disabled() and '示例面霜' not in page.locator('#cart-items').inner_text()
    checks.append('切组失败清除旧清单，不能误导入旧组商品')
    page.click('#disconnect');page.wait_for_selector('#loading',state='hidden')
    assert not page.locator('#consent').is_checked()
    checks.append('断开后撤销本机确认并清空选择')
    page.click('#try-demo');page.wait_for_selector('#loading',state='hidden');page.click('[data-tab=cart]')
    before=sum(p=='/api/cart/connect' for p,b in calls)
    page.click('#connect');page.wait_for_selector('#loading',state='hidden')
    assert sum(p=='/api/cart/connect' for p,b in calls)==before
    checks.append('演示模式不连接真实浏览器')
    assert not errors;checks.append('页面JavaScript异常为0')
    browser.close()
report={'passed':True,'checks':checks,'count':len(checks),'page_errors':errors,'scope':'Chromium in-memory actual frontend with synthetic API bridge; no real HTTP navigation or live Taobao account'}
(ROOT/'test-artifacts/local-browser').mkdir(parents=True,exist_ok=True)
(ROOT/'test-artifacts/local-browser/ui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
