"""Chromium UI + rendered-DOM fixtures. No real Taobao login or network access."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
os.environ['PROMOPATH_DATA_DIR']=tempfile.mkdtemp(prefix='tbpc-v05-ui-')
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright
from app import main
from app.models import Item, Project

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-artifacts/shopping';OUT.mkdir(parents=True,exist_ok=True)
checks=[]
def check(name,condition):
    if not condition:raise AssertionError(name)
    checks.append(name)

async def run():
    with TestClient(main.app,base_url='http://127.0.0.1:8765') as client:
      main.store.save(Project(items=[Item(id='a',title='测试商品A',shop_id='s1',shop_name='测试店',pricing_base_cents=10000,pricing_base_evidence='虚构',deposit_paid_cents=0,deposit_confirmed=True),Item(id='b',title='测试商品B',shop_id='s1',shop_name='测试店',pricing_base_cents=10000,pricing_base_evidence='虚构',deposit_paid_cents=0,deposit_confirmed=True)]))
      before=main.store.storage_hash()
      async with async_playwright() as pw:
        binary=os.getenv('TBPC_TEST_CHROMIUM','/usr/bin/chromium')
        # The container runs as root, so this test browser uses Playwright defaults.
        # This does not change the packaged application's sandbox configuration.
        browser=await pw.chromium.launch(executable_path=binary,headless=True)
        page=await browser.new_page(viewport={'width':1280,'height':900})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        async def bridge(url,options):
            response=client.request(options.get('method','GET'),url,headers=options.get('headers',{}),content=options.get('body'))
            return {'status':response.status_code,'body':response.json()}
        await page.expose_function('__apiBridge',bridge)
        html=(ROOT/'app/static/shopping.html').read_text().replace('__LOCAL_TOKEN__',main.TOKEN)
        html=html.replace('<link rel="stylesheet" href="/static/shopping.css">','').replace('<script defer src="/static/shopping.js"></script>','')
        await page.set_content(html)
        await page.add_style_tag(content=(ROOT/'app/static/shopping.css').read_text())
        await page.evaluate("() => {window.fetch=async(url,options={})=>{const r=await window.__apiBridge(url,options);return {ok:r.status>=200&&r.status<300,status:r.status,json:async()=>r.body};};}")
        await page.add_script_tag(content=(ROOT/'app/static/shopping.js').read_text())
        await page.locator('#loading').wait_for(state='hidden')
        check('购物车入口可见',await page.locator('#connect').is_visible())
        await page.locator('#try-demo').click();await page.locator('#loading').wait_for(state='hidden')
        check('一键演示含店铺、搭配和跨店优惠',(await page.locator('#result .total').inner_text())=='¥560.00')
        check('一键演示不改真实清单',main.store.storage_hash()==before)
        check('逐笔优惠明细显示正确',await page.locator('.discount-line').count()==3)
        await page.screenshot(path=str(OUT/'01-combination-result.png'),full_page=True)
        await page.locator('[data-tab=cart]').click();await page.select_option('#cart-group','taobao:skin');await page.locator('#loading').wait_for(state='hidden')
        check('示例淘宝分组筛选不混入其他组',await page.locator('#cart-items .item').count()==3 and '纸巾' not in await page.locator('#cart-items').inner_text())
        await page.select_option('#cart-group','shop:s3');await page.locator('#loading').wait_for(state='hidden')
        check('按店铺筛选与淘宝分组分别标识',await page.locator('#cart-items .item').count()==1 and '纸巾' in await page.locator('#cart-items').inner_text())
        check('演示不能导入真实清单',await page.locator('#import-cart').is_disabled())
        await page.screenshot(path=str(OUT/'02-cart-group.png'),full_page=True)
        await page.locator('#leave-demo').click();await page.locator('#loading').wait_for(state='hidden')
        check('退出演示保留真实数据',main.store.storage_hash()==before)
        await page.locator('[data-tab=offers]').click();await page.locator('#add-offer').click()
        await page.fill('#offer-name','测试店满200减20');await page.fill('#offer-threshold','200');await page.fill('#offer-amount','20');await page.fill('#evidence','测试券规则，非真实优惠');await page.check('#confirmed');await page.locator('#offer-form button[type=submit]').click();await page.locator('#offer-dialog').wait_for(state='hidden')
        check('简单表单可保存店铺优惠',len(main.store.load().rules)==1)
        await page.locator('#estimate').click();await page.locator('#loading').wait_for(state='hidden')
        check('实际表单优惠进入计算',(await page.locator('#result .total').inner_text())=='¥180.00')
        await page.set_viewport_size({'width':390,'height':844});await page.locator('[data-tab=offers]').click()
        check('窄屏没有横向溢出',await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        await page.locator('#add-offer').click();await page.select_option('#offer-type','tiered');await page.fill('#offer-name','多档测试');await page.fill('#offer-tiers','100,10\n200,30');await page.fill('#evidence','fixture');await page.check('#confirmed');await page.locator('#offer-form button[type=submit]').click();await page.locator('#offer-dialog').wait_for(state='hidden')
        check('多档优惠表单不要求隐藏金额',len(main.store.load().rules)==2)
        # Pure visible-DOM fixtures, not captured production Taobao DOM.
        fixture=await browser.new_page()
        await fixture.set_content('''<button role="tab" data-group-id="skin" aria-selected="true">护肤分组</button><button role="tab" data-group-id="other" aria-selected="false">其他</button>
        <div data-shop-id="s1" data-shop-name="护肤店"><div data-cart-item data-cart-id="r1"><a data-title="商品一" href="https://item.taobao.com/item.htm?id=11">商品一</a><div data-sku-text="规格A">A</div><input data-quantity="2"><span data-display-price="¥19.90" data-price-scope="unit">19.90</span></div>
        <div data-cart-item data-cart-id="r2"><a data-title="商品二" href="https://item.taobao.com/item.htm?id=12">商品二</a><input data-quantity="1"><span data-display-price="¥30" data-price-scope="line">30</span></div>
        <div data-cart-item data-cart-id="hidden" hidden><a data-title="隐藏商品">隐藏</a></div></div>''')
        script=(ROOT/'app/static/cart-reader.js').read_text();raw=await fixture.evaluate(script,{})
        check('DOM读取行、SKU数量、价格口径',len(raw['items'])==2 and raw['items'][0]['quantity']==2 and raw['items'][0]['price_scope']=='unit')
        check('DOM保留淘宝原生分组证据',raw['active_group_id']=='skin' and raw['items'][0]['group_ids']==['skin'])
        await fixture.evaluate("document.querySelectorAll('[data-cart-item]')[1].setAttribute('data-cart-id','r1')")
        raw=await fixture.evaluate(script,{})
        check('DOM重复标识不会静默合并',bool(raw['errors']))
        await fixture.set_content('<h1>登录后查看购物车</h1>');raw=await fixture.evaluate(script,{})
        check('不支持的页面明确警告而非证明空车',not raw['profile'] and bool(raw['warnings']) and raw['complete'] is False)
        check('浏览器页面无JS异常',not errors)
        await browser.close()
        report={'passed':checks,'count':len(checks),'page_errors':errors,'taobao_live_account_tested':False,
                'browser':'Chromium in-memory page and FastAPI bridge; real localhost navigation blocked by environment policy; synthetic cart DOM fixtures only',
                'sandbox_note':'Root container test browser uses Playwright defaults; packaged app security was not weakened.'}
        (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(run())
