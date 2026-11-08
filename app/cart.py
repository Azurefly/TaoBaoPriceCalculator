"""Read-only cart snapshots, native grouping provenance and non-destructive import."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import re
import uuid
from datetime import timedelta
from pydantic import Field
from fastapi import APIRouter, HTTPException, Request
from .models import Item, Project, StrictModel, utcnow
from .adapters.browser import money, allowed_url
from .cart_transition import wait_for_group
from .cart_reconcile import parse_discount_detail, reconcile, to_cents
from . import autopilot
from .promo_parse import parse_assets, suggest


class ConnectInput(StrictModel):
    local_consent: bool = False
    new_account: bool = False


class ReadInput(StrictModel):
    native_group_id: str | None = Field(default=None, max_length=300)


class BatchDetailInput(StrictModel):
    steps: list[list[str]] = Field(min_length=1, max_length=6)


class ImportInput(StrictModel):
    snapshot_id: str = Field(max_length=80)
    line_ids: list[str] = Field(min_length=1, max_length=28)
    group_id: str = 'view:current'
    account_confirmed: bool = False
    replace_existing: bool = False


def normalized_capture(raw: dict, connection_id: str) -> dict:
    rows = []
    errors = list(raw.get('errors', []))
    seen = set()
    for source in raw.get('items', []):
        line = str(source.get('line_id', ''))
        if not line or line in seen:
            errors.append('商品行标识缺失或重复；请重新读取，未自动合并。')
            continue
        seen.add(line)
        quantity = source.get('quantity')
        if type(quantity) is not int or not 1 <= quantity <= 999:
            quantity = None
        price = None
        try:
            value = money(source.get('display_price_text', ''))
            if source.get('price_scope') == 'line': price = value
            elif source.get('price_scope') == 'unit' and quantity is not None: price = value * quantity
        except ValueError:
            pass
        if price is not None and price > 1_000_000_000: price = None
        # 预售行在购物车里显示的是「定金」，不是货款；不能按单价×数量当作商品金额。
        deposit_label = '定金' in str(source.get('price_label') or '')
        if deposit_label:
            price = None
        ident = 'tb-' + hashlib.sha256((connection_id+'\0'+line).encode()).hexdigest()[:24]
        note = ''
        if deposit_label:
            ref = str(source.get('reference_price_text') or '').strip()
            note = f'购物车显示的是定金{("（参考总价" + ref + "）") if ref else ""}，不是货款；请用详情页核对优惠前货款与尾款。'
        rows.append({**source,'line_id':line,'id':ident,'quantity':quantity,'display_price_cents':price,
                     'can_import': bool(quantity and source.get('title') and not source.get('disabled')),
                     'price_label':str(source.get('price_label') or ''),
                     'reference_price_text':str(source.get('reference_price_text') or ''),
                     'display_note':note,
                     'pricing_base_cents':None,'pricing_base_evidence':'','deposit_confirmed':False})
    groups = [{'id':'view:current','name':'当前页面已读取的商品','source':'view'}]
    native_seen=set()
    for group in raw.get('groups', []):
        if not group.get('id') or group['id'] in native_seen:
            errors.append('淘宝分组标识缺失或重复，不能自动切换该分组。');continue
        native_seen.add(group['id'])
        groups.append({'id':'taobao:'+group['id'],'native_id':group['id'],
                       'name':group['name'],'source':'taobao','active':group.get('active',False)})
    shops = {}
    for row in rows:
        sid = row.get('shop_id')
        if sid: shops[sid]=row.get('shop_name') or sid
    groups += [{'id':'shop:'+sid,'name':name,'source':'shop'} for sid,name in sorted(shops.items())]
    raw_summary = raw.get('summary') or {}
    detail = parse_discount_detail(raw.get('detail_raw') or '')
    summary = {
        'payable_cents': to_cents(raw_summary.get('payable_text')) if raw_summary.get('payable_text') else None,
        'discount_cents': to_cents(raw_summary.get('discount_text')) if raw_summary.get('discount_text') else None,
        'goods_total_cents': (detail or {}).get('goods_total_cents'),
        'detail': detail,
        'note': '' if detail else '未读到优惠明细：请在网页上手动点开“优惠明细”后重新读取',
        'label_text': (raw_summary.get('raw') or '')[:120],
        'discount_label_text': (raw_summary.get('discount_raw') or '')[:120],
    }
    snapshot = {'snapshot_id':uuid.uuid4().hex,'captured_at':utcnow().isoformat(),'connection_id':connection_id,
            'items':rows,'groups':groups,'active_group_id':raw.get('active_group_id'),
            'complete':False,'errors':errors,'warnings':raw.get('warnings',[]),'profile':raw.get('profile',''),
            'summary':summary}
    snapshot['reconcile'] = reconcile(snapshot)
    return snapshot


def filter_rows(snapshot: dict, group_id: str):
    group = next((g for g in snapshot['groups'] if g['id']==group_id), None)
    if group is None: raise ValueError('指定分组不存在，未回退为全部商品')
    if group['source']=='shop':
        return [r for r in snapshot['items'] if r.get('shop_id') == group_id[5:]]
    if group['source']=='taobao':
        if snapshot.get('active_group_id') != group['native_id']:
            raise ValueError('该淘宝分组尚未读取，请先切换到这个分组并刷新')
        return [r for r in snapshot['items'] if group['native_id'] in r.get('group_ids',[])]
    return snapshot['items']


def imported_project(project: Project, snapshot: dict, body: ImportInput) -> Project:
    if not body.account_confirmed: raise ValueError('请确认当前淘宝账号和选择的商品正确')
    if len(body.line_ids) != len(set(body.line_ids)): raise ValueError('选择的商品行重复')
    if snapshot.get('errors'): raise ValueError('本次读取存在冲突，请重新读取后导入')
    allowed = {r['line_id']:r for r in filter_rows(snapshot,body.group_id)}
    if not set(body.line_ids) <= allowed.keys(): raise ValueError('选中商品不属于指定分组，未导入其他分组')
    if any(x.cart_scope and x.cart_scope != snapshot['connection_id'] for x in project.items):
        raise ValueError('清单中已有另一个淘宝账号/连接的商品。请先新建清单，不混用账号优惠。')
    out = project.model_copy(deep=True)
    out.account_context = 'taobao-' + snapshot['connection_id']
    known = {x.id:x for x in out.items}
    for line in body.line_ids:
        r=allowed[line]
        if not r['can_import']: raise ValueError('商品数量未识别或已失效，不能直接导入')
        item=Item(id=r['id'],title=r['title'],shop_id=r.get('shop_id',''),shop_name=r.get('shop_name',''),
            source_line_id=line,product_id=r.get('product_id',''),sku=r.get('sku',''),quantity=r['quantity'],
            display_price_cents=r['display_price_cents'],display_price_kind='unknown',
            cart_group_ids=r.get('group_ids',[]),cart_group_names=r.get('group_names',[]),cart_scope=snapshot['connection_id'],
            notes='购物车显示价格，不能作为未优惠金额。\n'+'\n'.join(r.get('observed_promotions',[])))
        if item.id in known:
            old=known[item.id]
            if item.id in out.paid_item_ids(): raise ValueError('商品已有付款记录，不能覆盖')
            if not body.replace_existing: continue
            unchanged=(old.quantity,old.sku,old.shop_id)==(item.quantity,item.sku,item.shop_id)
            if unchanged:
                item=item.model_copy(update={key:getattr(old,key) for key in ('pricing_base_cents','pricing_base_evidence',
                    'deposit_paid_cents','deposit_confirmed','required','tags')})
            known[item.id]=item
        else: known[item.id]=item
    if len(known)>28: raise ValueError('一次购物计划最多28行。请缩小分组或减少选择；购物车查看不会截成28行。')
    out.items=list(known.values())
    return Project.model_validate(out.model_dump())


class CartService:
    def __init__(self,browser,root:Path):
        self.browser=browser; self.root=root; self.snapshot=None
        self.connection_id=uuid.uuid4().hex
        self.identity_path=browser.data_dir/'cart-context.json'
        try:
            saved=json.loads(self.identity_path.read_text('utf-8'))['context']
            if re.fullmatch(r'[0-9a-f]{32}',saved): self.connection_id=saved
        except (OSError,ValueError,KeyError,TypeError): pass
        self.last_read=0.0

    async def read(self,native_group_id=None):
        async with self.browser.lock:
            self.snapshot=None
            page=await self.browser.page()
            script=(self.root/'app/static/cart-reader.js').read_text('utf-8')
            config=self.browser.config.model_dump()
            switched_snapshot = None
            if native_group_id:
                before=await page.evaluate(script,config)
                group=next((g for g in before.get('groups',[]) if g['id']==native_group_id),None)
                if group is None: raise ValueError('当前网页没有该淘宝分组，不能按相同名字猜测')
                if not group.get('active'):
                    tabs=page.locator(config.get('group_tabs') or '[role="tab"][data-group-id], [data-cart-group-id][role="tab"]')
                    matches=[]
                    for i in range(await tabs.count()):
                        tab=tabs.nth(i)
                        gid=await tab.get_attribute('data-cart-group-id') or await tab.get_attribute('data-group-id')
                        if gid==native_group_id and await tab.is_visible(): matches.append(tab)
                    if len(matches)!=1: raise ValueError('分组入口不唯一，已停止切换')
                    if await matches[0].get_attribute('role')!='tab': raise ValueError('仅允许切换已识别的分组标签')
                    await self.browser.check_page(page)
                    await matches[0].click(timeout=5000)
                    async def reread():
                        checked_page = await self.browser.page()
                        return await checked_page.evaluate(script,config)
                    switched_snapshot = await wait_for_group(reread, page.wait_for_timeout, native_group_id, before)
            raw=switched_snapshot if switched_snapshot is not None else await page.evaluate(script,config)
            if not raw.get('profile'):
                raise ValueError('当前购物车页面尚未适配，无法确认商品。请使用手动录入；不会将识别失败当成空购物车。')
            if native_group_id and raw.get('active_group_id')!=native_group_id:
                raise ValueError('网页未确认进入目标分组，已停止，未读取全部商品替代')
            self.snapshot=normalized_capture(raw,self.connection_id)
            return self.snapshot


def create_cart_router(store,browser,root:Path):
    router=APIRouter();service=CartService(browser,root)

    @router.get('/api/cart/status')
    async def status():
        opened=bool(browser.context)
        state='closed'; note='点击连接，登录和扫码都在你自己的浏览器里完成。'
        if opened:
            try:
                page=await browser.page(); state='ready'; note='请在浏览器打开购物车，再点读取。'
            except ValueError as error:
                state='needs_attention';note=str(error);service.snapshot=None
        else:
            service.snapshot=None
        return {'opened':opened,'state':state,'note':note,'mode':'local_browser',
                'local_consent':browser.permitted(),'official_api_required':False,
                'snapshot':service.snapshot,'no_payment_capability':True}

    @router.post('/api/cart/connect')
    async def connect(body:ConnectInput):
        if not body.local_consent: raise ValueError('请确认仅在本机读取你本人的购物车。')
        async with browser.lock:
            if body.new_account:
                if browser.context: raise ValueError('切换账号前请先断开连接，再到浏览器切换登录。')
                service.connection_id=uuid.uuid4().hex
            if not browser.context: service.snapshot=None
            result=await browser.launch(local_consent=True)
            service.identity_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            service.identity_path.write_text(json.dumps({'context':service.connection_id}),encoding='utf-8')
            try: service.identity_path.chmod(0o600)
            except OSError: pass
            return result

    @router.post('/api/cart/open')
    async def open_cart():
        async with browser.lock:
            service.snapshot=None
            page=await browser.page()
            await page.goto('https://cart.taobao.com/cart.htm',wait_until='domcontentloaded',timeout=45000)
        return {'opened':True}

    @router.post('/api/cart/read')
    async def read(body:ReadInput):
        now=asyncio.get_running_loop().time()
        if now-service.last_read<2: raise HTTPException(429,'刚刚读取过，请稍后再刷新')
        service.last_read=now
        return await service.read(body.native_group_id)

    @router.post('/api/cart/import')
    async def import_cart(body:ImportInput,request:Request):
        # A closed session or verification screen invalidates the old page snapshot.
        try:
            await browser.page()
        except ValueError:
            service.snapshot=None
            raise
        snap=service.snapshot
        if not snap or snap['snapshot_id']!=body.snapshot_id: raise ValueError('购物车快照已变化，请重新选择')
        from datetime import datetime
        if utcnow()-datetime.fromisoformat(snap['captured_at'])>timedelta(minutes=15):
            raise ValueError('购物车读取已超过15分钟，请刷新后重选')
        revision=request.headers.get('x-project-revision')
        if not revision: raise HTTPException(428,'请刷新页面后再导入')
        with store.lock:
            store.assert_revision(revision)
            project=imported_project(store.load(),snap,body)
            new_revision=store.save(project,revision)
        return {'saved':True,'revision':new_revision,'item_count':len(project.items)}

    @router.post('/api/cart/autopilot')
    async def autopilot_run():
        """扫码后直接返回当前勾选商品的方案；保留勾选，不代下单、不付款。

        购物车行价是已分摊优惠后的行实付，故只把它当作方案金额，不再次扣减
        页面上已经计入的官方立减/店铺券。规则文案只展示，不自动用于二次扣款。
        """
        def need_login(url: str) -> dict:
            return {'state': 'need_login', 'login_url': url,
                    'hint': '请在浏览器里扫码；完成后本页自动继续，不需要再点按钮。'}
        try:
            async with browser.lock:
                opened = False
                if not browser.context:
                    await browser.launch(local_consent=True)
                    opened = True
                if not browser.context.pages:
                    raise ValueError('浏览器没有可用页面')
                page = browser.context.pages[-1]
                if 'taobao.com' not in page.url and 'tmall.com' not in page.url:
                    await page.goto('https://cart.taobao.com/cart.htm', wait_until='domcontentloaded', timeout=45000)
                if not await autopilot.wait_login(page, browser):
                    return need_login(page.url)
                page = await browser.page()
                if 'cart.taobao.com' not in page.url:
                    await page.goto('https://cart.taobao.com/cart.htm', wait_until='domcontentloaded', timeout=45000)
                await autopilot.wait_rows(page)

                # 尊重用户已勾选的商品。只有一行都没选时才自动全选可购买商品。
                boxes = page.locator('div.trade-cart-item-info input.ant-checkbox-input')
                selected_before = 0
                for i in range(await boxes.count()):
                    try:
                        selected_before += bool(await boxes.nth(i).is_checked())
                    except Exception:
                        pass
                auto_selected = 0
                if not selected_before:
                    auto_selected = await autopilot.select_all_rows(page)
                await page.wait_for_timeout(2200)
                detail_raw = await autopilot.read_discount_detail(page)
                await page.wait_for_timeout(500)
                script = (root / 'app/static/cart-reader.js').read_text('utf-8')
                raw = await page.evaluate(script, browser.config.model_dump())
                raw['detail_raw'] = detail_raw
                if not raw.get('profile'):
                    raise ValueError('当前购物车页面尚未适配，无法读取')
                snapshot = normalized_capture(raw, service.connection_id)
                snapshot['shopTexts'] = raw.get('shopTexts') or {}
                snapshot['cartTexts'] = raw.get('cartTexts') or []
                service.snapshot = snapshot
                await browser.save_state()
        except Exception as error:
            text = str(error)
            if 'has been closed' in text or 'Target page' in text:
                try:
                    await browser.close()
                except Exception:
                    pass
                return {'state': 'browser_closed',
                        'hint': '浏览器已关闭，连接已重置；再点一次即可，通常无需重新扫码。'}
            raise

        selected_lines = [r['line_id'] for r in snapshot['items']
                          if r.get('selected') and r.get('can_import')]
        wanted = [r['line_id'] for r in snapshot['items'] if r.get('can_import')]
        if not wanted:
            raise ValueError('当前没有可导入的商品')
        deferred = [{'line_id': r['line_id'], 'title': r.get('title'),
                     'quantity': r.get('quantity'), 'reference_price_text': r.get('reference_price_text')}
                    for r in snapshot['items'] if '定金' in str(r.get('price_label') or '')]
        deferred_lines = {x['line_id'] for x in deferred}
        _, revision = store.snapshot()
        with store.lock:
            project = store.load()
            body = ImportInput(snapshot_id=snapshot['snapshot_id'], line_ids=wanted,
                               group_id='view:current', account_confirmed=True, replace_existing=True)
            project = imported_project(project, snapshot, body)
            # 每次以当前购物车为准，移除已不在页面中的旧行与旧自动规则。
            current_ids = {r['id'] for r in snapshot['items']}
            project.items = [it for it in project.items if it.id in current_ids]
            project.rules = [r for r in project.rules
                             if not r.id.startswith('auto-') and '自动识别' not in r.source_note]
            for item in project.items:
                is_selected = item.source_line_id in selected_lines
                item.calculation_state = ('presale_pending' if item.source_line_id in deferred_lines
                                          else 'selected' if is_selected else 'unselected')
                item.required = is_selected and item.source_line_id not in deferred_lines
                if item.source_line_id in deferred_lines:
                    item.required = False
                    item.pricing_base_cents = None
                    item.pricing_base_evidence = '预售：需详情页核对尾款与定金口径'
                    item.deposit_confirmed = False
                    item.deposit_paid_cents = None
                else:
                    # 行实付就是本次计算金额，不从参考价重建，也不重复扣官方立减。
                    item.pricing_base_cents = item.display_price_cents
                    item.pricing_base_evidence = '购物车行实付（已分摊优惠；未再重复扣券）'
                    item.deposit_confirmed = item.pricing_base_cents is not None
                    item.deposit_paid_cents = 0 if item.deposit_confirmed else None
            project = Project.model_validate(project.model_dump())
            store.save(project, revision)

        # 文案与券包用于展示和后续核对，不自动变成会再次扣款的规则。
        drafts = suggest([r for r in snapshot['items'] if r.get('can_import')],
                         snapshot.get('shopTexts') or {}, snapshot.get('cartTexts') or [])
        suggested = []
        for index, draft in enumerate(drafts):
            payload = {k: v for k, v in draft.items() if k != 'scope'}
            payload['id'] = f'draft-{index}'
            try:
                suggested.append(Rule(**payload).model_dump(mode='json'))
            except Exception:
                continue
        detail = (snapshot.get('summary') or {}).get('detail') or {}
        assets = parse_assets(snapshot.get('cartTexts') or [],
                              (detail.get('shop_lines') or []) + (detail.get('platform_lines') or []))
        plan = None
        try:
            from .pricing import simulate_project
            from .workflow import compare_strategies
            from .models import SolveOptions, utcnow
            current, _ = store.snapshot()
            simulated, info = await asyncio.to_thread(simulate_project, current, 300, None, 6)
            comparison = await asyncio.to_thread(compare_strategies, simulated,
                SolveOptions(mode='model', starts_at=utcnow(), step_seconds=60, max_seconds=6))
            plan = {'project': simulated.model_dump(mode='json'), 'comparison': comparison,
                    'simulation': info, 'summary': snapshot.get('summary'), 'model_only': True,
                    'notice': '按购物车已分摊行实付计算；官方立减等没有重复扣减。最终以淘宝结算页为准。'}
        except Exception as error:
            plan = {'error': str(error), 'comparison': {'strategies': []}, 'simulation': {'warnings': [str(error)]}}

        # 逐笔回读真实优惠明细：方案建议拆成几笔，就分别勾选那几件、读该笔自己的优惠明细。
        # 每笔都是淘宝真实数据（跨店满减/红包在拆单后会重新分摊），读完还原原勾选。
        async with browser.lock:
            batch_details = []
            try:
                page = await browser.page()
                await autopilot.wait_rows(page)
                original = {r['line_id'] for r in snapshot['items'] if r.get('selected')}
                best = next((s for s in plan.get('comparison', {}).get('strategies', [])
                             if s.get('key') == 'optimized'), None)
                steps = (best or {}).get('result', {}).get('steps') or []
                script = (root / 'app/static/cart-reader.js').read_text('utf-8')
                cfg = browser.config.model_dump()
                for step in steps[:6]:
                    lines = set()
                    for it in project.items:
                        if it.id in step.get('item_ids', []) and it.source_line_id:
                            lines.add(it.source_line_id)
                    if not lines:
                        continue
                    await autopilot.set_checked_lines(page, lines)
                    await page.wait_for_timeout(2500)
                    detail_raw = await autopilot.read_discount_detail(page)
                    raw = await page.evaluate(script, cfg)
                    raw_summary = raw.get('summary') or {}
                    batch_details.append({
                        'item_ids': sorted(step.get('item_ids', [])),
                        'payable_cents': to_cents(raw_summary.get('payable_text')) if raw_summary.get('payable_text') else None,
                        'discount_cents': to_cents(raw_summary.get('discount_text')) if raw_summary.get('discount_text') else None,
                        'detail': parse_discount_detail(detail_raw),
                    })
                if original:
                    await autopilot.set_checked_lines(page, original)
                    await page.wait_for_timeout(1500)
            except Exception as error:
                batch_details = []
                plan.setdefault('simulation', {}).setdefault('warnings', []).append(
                    f'分笔优惠明细读取失败（不影响金额）：{type(error).__name__}')
            if batch_details:
                plan['batch_details'] = batch_details

        return {'state': 'ok', 'snapshot': snapshot, 'imported': len(project.items),
                'selected_count': len(selected_lines), 'auto_selected': auto_selected,
                'suggested': suggested, 'assets': assets, 'deferred': deferred, 'plan': plan,
                'opened': opened, 'reconcile': snapshot.get('reconcile'), 'summary': snapshot.get('summary'),
                'note': '按当前勾选商品直接出方案；未提交订单、未付款。'}

    @router.post('/api/cart/requote')
    async def requote():
        """把本地「参与计算」商品的勾选同步到购物车，回读真实重摊后更新方案金额。

        跨店满减按商品金额比例重摊，所以换勾选组合后行实付会变；只有回读购物车
        才能拿到准确的新合计。此接口只做勾选/取消勾选与读取，不点结算、不提交、不付款。
        """
        if not browser.context:
            raise ValueError('请先连接购物车')
        page = await browser.page()
        await autopilot.wait_rows(page)
        current = store.load()
        keep = {it.source_line_id for it in current.items
                if it.required and it.source_line_id and it.calculation_state != 'presale_pending'}
        await autopilot.set_checked_lines(page, keep)
        await page.wait_for_timeout(2500)
        detail_raw = await autopilot.read_discount_detail(page)
        await page.wait_for_timeout(400)
        script = (root / 'app/static/cart-reader.js').read_text('utf-8')
        raw = await page.evaluate(script, browser.config.model_dump())
        raw['detail_raw'] = detail_raw
        rows = {}
        for row in raw.get('items', []):
            qty = row.get('quantity') or 1
            unit = to_cents(row.get('display_price_text')) if row.get('display_price_text') else None
            rows[row['line_id']] = {'line_total_cents': (unit * qty) if unit else None,
                                    'quantity': qty, 'selected': row.get('selected', False)}
        updated = 0
        with store.lock:
            cur = store.load()
            for it in cur.items:
                if it.source_line_id in rows and rows[it.source_line_id]['line_total_cents'] is not None:
                    it.pricing_base_cents = rows[it.source_line_id]['line_total_cents']
                    it.pricing_base_evidence = '回读购物车重摊后行实付'
                    it.deposit_confirmed = True
                    if it.deposit_paid_cents is None:
                        it.deposit_paid_cents = 0
                    updated += 1
            raw_summary = raw.get('summary') or {}
            payable = to_cents(raw_summary.get('payable_text')) if raw_summary.get('payable_text') else None
            discount = to_cents(raw_summary.get('discount_text')) if raw_summary.get('discount_text') else None
            # 分摊取整会留下 1 分尾差：把参与计算商品的行实付之和对齐到淘宝底部合计，
            # 让方案金额与淘宝完全一致（差额归集到金额最大的行）。
            # 仅当回读到的合计有效（>0）且差额在合理范围内才对齐，避免勾选失败把行减成负数。
            req_items = [it for it in cur.items if it.required and it.pricing_base_cents is not None]
            if payable and payable > 0 and req_items:
                row_sum = sum(it.pricing_base_cents for it in req_items)
                diff = payable - row_sum
                if abs(diff) <= min(100, row_sum):
                    largest = max(req_items, key=lambda x: x.pricing_base_cents)
                    largest.pricing_base_cents += diff
            store.save(Project.model_validate(cur.model_dump()))
        detail = parse_discount_detail(detail_raw)
        # 浮层偶发读不到时，回退到上一次快照的明细，避免方案页明细丢失
        if detail is None and service.snapshot:
            detail = (service.snapshot.get('summary') or {}).get('detail')
        return {'state': 'ok', 'payable_cents': payable, 'discount_cents': discount,
                'detail': detail, 'updated': updated, 'keep': sorted(keep),
                'note': '已按当前参与计算商品回读购物车真实重摊；未提交订单、未付款。'}

    @router.post('/api/cart/batch-details')
    async def batch_details(body: BatchDetailInput):
        """按给定的每一笔（item_id 列表）逐笔勾选购物车、回读该笔自己的优惠明细。

        拆单后跨店满减/红包会重新分摊，只有逐笔回读才能拿到每笔真实数据；读完还原原勾选。
        """
        if not browser.context:
            raise ValueError('请先连接购物车')
        page = await browser.page()
        await autopilot.wait_rows(page)
        current = store.load()
        id_to_line = {it.id: it.source_line_id for it in current.items if it.source_line_id}
        original = {it.source_line_id for it in current.items
                    if it.required and it.source_line_id and it.calculation_state != 'presale_pending'}
        script = (root / 'app/static/cart-reader.js').read_text('utf-8')
        cfg = browser.config.model_dump()
        results = []
        try:
            for step in body.steps:
                lines = {id_to_line[i] for i in step if i in id_to_line}
                if not lines:
                    continue
                await autopilot.set_checked_lines(page, lines)
                await page.wait_for_timeout(2500)
                detail_raw = await autopilot.read_discount_detail(page)
                raw = await page.evaluate(script, cfg)
                raw_summary = raw.get('summary') or {}
                results.append({
                    'item_ids': sorted(step),
                    'payable_cents': to_cents(raw_summary.get('payable_text')) if raw_summary.get('payable_text') else None,
                    'discount_cents': to_cents(raw_summary.get('discount_text')) if raw_summary.get('discount_text') else None,
                    'detail': parse_discount_detail(detail_raw),
                })
        finally:
            if original:
                try:
                    await autopilot.set_checked_lines(page, original)
                    await page.wait_for_timeout(1200)
                except Exception:
                    pass
        return {'batch_details': results, 'note': '逐笔优惠明细已回读；未提交订单、未付款。'}

    @router.post('/api/cart/disconnect')
    async def disconnect():
        async with browser.lock:
            service.snapshot=None
            await browser.close()
        return {'closed':True,'note':'已停止读取。登录资料仅留在本机独立浏览器目录，可在该浏览器退出淘宝账号。'}
    return router
