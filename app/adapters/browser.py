"""Experimental visible-DOM adapter. No private API replay, stealth or payment code.

Disabled unless TAOBAO_PLATFORM_PERMISSION_REFERENCE is set to a real permission
reference by the deployer. This is an attestation gate, NOT official authorization
verification. Site-specific selectors require calibration and must fail closed.
"""
from __future__ import annotations
import asyncio
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from datetime import timedelta
from pydantic import Field
from ..models import StrictModel, Project, Quote, utcnow


def money(text: str) -> int:
    # Require one complete monetary token; never take the first number from a label.
    m = re.fullmatch(r'(?:[¥￥]\s*)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(?:元)?', text.strip())
    if not m:
        raise ValueError('金额节点必须只包含一个有效非负金额（最多两位小数）；请校准选择器')
    value = Decimal(m[1].replace(',', '') + ('.' + m[2] if m[2] else ''))
    return int(value * 100)


def allowed_url(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or '').lower()
    return p.scheme == 'https' and not p.username and not p.password and any(
        host == suffix or host.endswith('.' + suffix) for suffix in ('taobao.com', 'tmall.com', 'tmall.hk'))


class BrowserConfig(StrictModel):
    cart_url: str = 'https://cart.taobao.com/cart.htm'
    rows: str = ''
    row_id_attribute: str = 'data-id'
    title: str = ''
    sku: str = ''
    quantity: str = ''
    display_price: str = ''
    checkbox: str = 'input[type="checkbox"]'
    checkout_button: str = ''
    checkout_total: str = ''
    checkout_rows: str = ''
    checkout_row_id_attribute: str = 'data-id'
    checkout_quantity: str = ''
    # Required for automatic cart trials. User must verify loaded/selected lines independently.
    cart_complete_marker: str = ''
    selectors_calibrated: bool = False
    auto_trials_enabled: bool = False
    trial_delay_seconds: int = Field(default=10, ge=8, le=120)


class BrowserAdapter:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.pw = None
        self.context = None
        self.lock = asyncio.Lock()
        self.last_trial = 0.0
        self.config = BrowserConfig()

    def permitted(self) -> bool:
        return bool(os.getenv('TAOBAO_PLATFORM_PERMISSION_REFERENCE', '').strip())

    def require_permission(self):
        if not self.permitted():
            raise ValueError('浏览器适配器默认关闭。淘宝现行规则要求平台书面许可；普通账号登录/用户同意不是该许可。取得许可后按README配置。')

    async def launch(self):
        self.require_permission()
        if self.context:
            return {'opened': True}
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        try:
            profile = self.data_dir / 'browser-profile'
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            channel = os.getenv('PROMOPATH_BROWSER_CHANNEL', '').strip()
            if channel and channel not in ('chrome', 'msedge'):
                raise ValueError('浏览器channel只允许chrome或msedge')
            self.context = await self.pw.chromium.launch_persistent_context(str(profile), headless=False,
                viewport={'width': 1380, 'height': 940}, accept_downloads=False,
                **({'channel': channel} if channel else {}))
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await page.goto('https://www.taobao.com/', wait_until='domcontentloaded', timeout=45000)
        except Exception:
            if self.context:
                await self.context.close()
                self.context = None
            await self.pw.stop()
            self.pw = None
            raise
        return {'opened': True, 'note': '请在打开的浏览器中自行扫码登录；程序不读取密码或支付凭证。'}

    async def close(self):
        if self.context:
            await self.context.close()
            self.context = None
        if self.pw:
            await self.pw.stop()
            self.pw = None

    async def page(self):
        self.require_permission()
        if not self.context or not self.context.pages:
            raise ValueError('尚未打开授权浏览器')
        page = self.context.pages[-1]
        if not allowed_url(page.url):
            raise ValueError('当前页不是允许的淘宝/天猫HTTPS页面；暂停操作')
        # Pause on explicit verification surfaces. No CAPTCHA bypass, no stealth.
        for selector in ('#baxia-dialog-content', '#nc_1_wrapper', '[data-testid="captcha"]'):
            loc = page.locator(selector)
            if await loc.count() and await loc.first.is_visible():
                raise ValueError('检测到安全验证；已暂停，请按平台提示人工处理')
        if re.search(r'(?:login|passport)\.', urlparse(page.url).hostname or ''):
            raise ValueError('仍处于登录页；请本人完成登录')
        return page

    async def _quantity(self, row, selector: str) -> int:
        node = row.locator(selector)
        if await node.count() != 1:
            raise ValueError('数量节点不唯一，请校准')
        tag = await node.evaluate('(e)=>e.tagName.toLowerCase()')
        raw = await node.input_value() if tag in ('input','select') else await node.inner_text()
        if not re.fullmatch(r'\s*[1-9]\d{0,2}\s*', raw):
            raise ValueError('数量节点必须是1至999的整数；不从规格或标题猜数量')
        return int(raw)

    async def capture_cart(self):
        async with self.lock:
            page = await self.page()
            cfg = self.config
            if not cfg.rows or not cfg.title:
                raise ValueError('先配置商品行/标题等DOM选择器；当前页面未做真实适配，不猜测')
            rows = page.locator(cfg.rows)
            count = await rows.count()
            if not 0 < count <= 100:
                raise ValueError('未识别商品行或超过安全上限；请校准选择器')
            out = []
            seen = set()
            for i in range(count):
                row = rows.nth(i)
                ident = await row.get_attribute(cfg.row_id_attribute)
                if not ident or ident in seen:
                    raise ValueError('商品行缺少唯一稳定ID；同款不同SKU必须能区分')
                seen.add(ident)
                title = await row.locator(cfg.title).inner_text(timeout=5000)
                sku = await row.locator(cfg.sku).inner_text(timeout=5000) if cfg.sku else ''
                price = money(await row.locator(cfg.display_price).inner_text(timeout=5000)) if cfg.display_price else None
                # A page title/price is evidence of display, not a valid discount base.
                out.append({'id': ident, 'title': title.strip()[:400], 'sku': sku.strip(),
                            'display_price_cents': price, 'display_price_kind': 'unknown',
                            'quantity': await self._quantity(row,cfg.quantity) if cfg.quantity else 1,
                            'notes': '授权DOM采集；数量/定金/优惠口径需核对。仅当前加载的商品行。'})
            return {'items': out, 'complete': False,
                    'warning': '只证明当前DOM已加载行；虚拟滚动/折叠/失效商品可能未包含，不自动声称全量。'}

    async def _observe(self, project: Project, expected_ids: list[str]):
        page = await self.page()
        cfg = self.config
        if not cfg.checkout_total or not cfg.checkout_rows:
            raise ValueError('必须校准结算应付金额节点与结算商品行，不能从总计/节省/定金文案猜金额')
        rows = page.locator(cfg.checkout_rows)
        actual = [await rows.nth(i).get_attribute(cfg.checkout_row_id_attribute) for i in range(await rows.count())]
        if None in actual or sorted(actual) != sorted(expected_ids):
            raise ValueError('结算商品ID与候选组不一致；已停止，不接受这笔报价')
        if cfg.checkout_quantity:
            quantities = {x.id:x.quantity for x in project.items}
            for i, ident in enumerate(actual):
                if await self._quantity(rows.nth(i),cfg.checkout_quantity) != quantities[ident]:
                    raise ValueError('结算页数量与项目不一致，拒绝采价')
        cash = money(await page.locator(cfg.checkout_total).inner_text(timeout=5000))
        at = utcnow()
        q = Quote(id='capture-' + uuid.uuid4().hex[:12], item_ids=expected_ids, cash_due_cents=cash,
                  source='draft', ledger_complete=False, portable_state=False,
                  observed_balances={r.id:r.available for r in project.resources},
                  context_fingerprint=project.fingerprint(), captured_at=at, valid_until=at+timedelta(minutes=5),
                  evidence_note='浏览器结算页金额采集；需人工核对每张券/红包/购物金的实际消耗，才能转为checkout报价',
                  notes=['未提交订单，未付款。仅看到总额无法证明跨批次权益独立。',
                         '已按配置核对结算数量' if cfg.checkout_quantity else '未配置结算数量选择器，须人工核对SKU数量'])
        return q

    async def observe(self, project: Project, expected_ids: list[str]):
        async with self.lock:
            return await self._observe(project, expected_ids)

    async def trial(self, project: Project, expected_ids: list[str]):
        async with self.lock:
            cfg = self.config
            self.require_permission()
            if not cfg.selectors_calibrated or not cfg.auto_trials_enabled:
                raise ValueError('自动试算需先完成选择器校准，并显式启用；默认关闭')
            if not allowed_url(cfg.cart_url) or not cfg.cart_complete_marker:
                raise ValueError('购物车URL或全量加载标记未配置')
            if not expected_ids or not set(expected_ids) <= {x.id for x in project.items}:
                raise ValueError('试算组包含未知商品')
            page = await self.page()
            loop = asyncio.get_running_loop()
            wait = self.last_trial + cfg.trial_delay_seconds - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_trial = loop.time()
            await page.goto(cfg.cart_url, wait_until='domcontentloaded', timeout=45000)
            await self.page()
            if not await page.locator(cfg.cart_complete_marker).is_visible():
                raise ValueError('不能证明购物车已完整加载，拒绝自动勾选')
            rows = page.locator(cfg.rows)
            mapping = {}
            for i in range(await rows.count()):
                row = rows.nth(i)
                ident = await row.get_attribute(cfg.row_id_attribute)
                if not ident or ident in mapping:
                    raise ValueError('购物车商品行ID重复或缺失')
                mapping[ident] = row
            # The project must describe every loaded selectable row, otherwise hidden selections may leak in.
            if set(mapping) != {x.id for x in project.items} or not set(expected_ids) <= mapping.keys():
                raise ValueError('购物车和项目商品集合不一致，请重新采集并核对')
            for ident, row in mapping.items():
                cb = row.locator(cfg.checkbox)
                if await cb.count() != 1:
                    raise ValueError('每行必须仅对应一个原生勾选框')
                tag = await cb.evaluate('(e)=>e.tagName.toLowerCase()+":"+e.type')
                if tag != 'input:checkbox':
                    raise ValueError('仅支持原生checkbox，不对未知组件执行猜测点击')
                await cb.set_checked(ident in expected_ids, timeout=5000)
            button = page.locator(cfg.checkout_button)
            if await button.count() != 1:
                raise ValueError('结算按钮不唯一')
            label = (await button.inner_text()).strip()
            if not re.fullmatch(r'结算\s*(?:[（(]\s*\d+\s*[)）])?', label):
                raise ValueError('按钮文字不在安全白名单；只允许“结算”，绝不点击提交订单/支付/确认收货')
            # This is the only non-checkbox automated click in the adapter.
            await button.click(timeout=10000)
            await page.wait_for_timeout(1500)
            return await self._observe(project, expected_ids)
