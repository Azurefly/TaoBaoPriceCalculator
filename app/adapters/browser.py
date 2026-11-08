"""Experimental visible-DOM adapter. No private API replay, stealth or payment code.

Uses the user's own visible browser session after local confirmation. No platform
OAuth, API key or permission-reference form is required. A local confirmation is
not a claim about platform authorization. Unknown pages and challenges stop actions.
"""
from __future__ import annotations
import asyncio
import os
import re
import uuid
import json
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


def cart_line_map(project: Project) -> dict[str, str]:
    """Map local immutable IDs to observed DOM IDs; never match by product title."""
    mapping = {item.id: item.source_line_id or item.id for item in project.items}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('清单中存在重复的网页商品行标识，请重新导入，不能猜测对应关系')
    return mapping


class BrowserConfig(StrictModel):
    cart_url: str = 'https://cart.taobao.com/cart.htm'
    rows: str = ''
    row_id_attribute: str = 'data-id'
    group_tabs: str = ''
    shop_name: str = ''
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
        self.local_consent = False
        # 参考通行的 storage_state 方案：登录一次后把会话快照落盘，
        # profile 丢失或换机时可以直接恢复，不用重新扫码。
        self.state_path = data_dir / 'browser-state.json'

    def permitted(self) -> bool:
        """Compatibility name: local user consent only, NOT platform permission."""
        return self.local_consent

    def require_permission(self):
        """Compatibility alias retained for existing internal callers."""
        if not self.local_consent:
            raise ValueError('请先点击“打开浏览器登录淘宝”，确认仅操作你本人的购物车。')

    async def launch(self, *, local_consent: bool = False):
        if local_consent is True:
            self.local_consent = True
        self.require_permission()
        if self.context:
            return {'opened': True}
        if self.pw:
            await self.pw.stop()
            self.pw = None
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        try:
            profile = self.data_dir / 'browser-profile'
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            # 上一轮被强杀时 Chrome 会留下 SingletonLock，导致之后一律“打不开浏览器”。
            # 只在占用进程已不存在时清掉，避免打断真正在用的窗口。
            lock = profile / 'SingletonLock'
            try:
                if lock.is_symlink() or lock.exists():
                    target = os.readlink(lock) if lock.is_symlink() else ''
                    pid = int(target.rsplit('-', 1)[-1]) if '-' in target else 0
                    alive = False
                    if pid:
                        try:
                            os.kill(pid, 0)
                            alive = True
                        except OSError:
                            alive = False
                    if not alive:
                        lock.unlink()
            except Exception:
                pass
            channel = os.getenv('PROMOPATH_BROWSER_CHANNEL', '').strip()
            if channel and channel not in ('chrome', 'msedge'):
                raise ValueError('浏览器channel只允许chrome或msedge')
            choices = list(dict.fromkeys(([channel] if channel else []) + ['chrome', 'msedge', '']))
            for candidate in choices:
                try:
                    # 注意：launch_persistent_context 不接受 storage_state 参数
                    # （会 TypeError，导致所有 channel 都启动失败），这里改为启动后注入 Cookie。
                    self.context = await self.pw.chromium.launch_persistent_context(str(profile), headless=False,
                        chromium_sandbox=True, viewport={'width': 1380, 'height': 940}, accept_downloads=False,
                        **({'channel': candidate} if candidate else {}))
                    break
                except Exception:
                    continue
            if not self.context:
                raise ValueError('未能打开浏览器。请安装Chrome或Edge，并关闭使用同一工具登录资料的窗口后重试。')
            # 会话快照恢复：profile 被清掉或换机时，凭上次登录的 Cookie 直接恢复登录态。
            if self.state_path.exists():
                try:
                    state = json.loads(self.state_path.read_text('utf-8'))
                    cookies = state.get('cookies') or []
                    if cookies:
                        await self.context.add_cookies(cookies)
                except Exception:
                    pass
            opened_context = self.context
            def closed():
                if self.context is opened_context:
                    self.context = None
                    self.local_consent = False
            self.context.on('close', closed)
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await page.goto('https://cart.taobao.com/cart.htm', wait_until='domcontentloaded', timeout=45000)
        except Exception:
            if self.context:
                await self.context.close()
                self.context = None
            await self.pw.stop()
            self.pw = None
            self.local_consent = False
            raise
        return {'opened': True, 'note': '请在打开的浏览器中自行扫码登录；程序不读取密码或支付凭证。'}

    async def save_state(self) -> bool:
        """登录成功后存一份会话快照（仅本机，含 Cookie，已被 .gitignore 覆盖）。"""
        if not self.context:
            return False
        try:
            await self.context.storage_state(path=str(self.state_path))
            try:
                self.state_path.chmod(0o600)
            except OSError:
                pass
            return True
        except Exception:
            return False

    @staticmethod
    def logged_in(page) -> bool:
        """只看域名判断是否已离开登录页，不发请求。"""
        try:
            host = urlparse(page.url).hostname or ''
        except Exception:
            return False
        return bool(host) and not re.search(r'(^|\.)(login|passport)\.', host)

    async def close(self):
        # Revoke permission before awaiting close; new actions cannot start.
        self.local_consent = False
        context, pw = self.context, self.pw
        self.context = None
        self.pw = None
        try:
            if context:
                await context.close()
        finally:
            if pw:
                await pw.stop()

    async def page(self):
        self.require_permission()
        if not self.context or not self.context.pages:
            raise ValueError('尚未打开本机浏览器，请先点击“打开浏览器登录淘宝”')
        page = self.context.pages[-1]
        if not allowed_url(page.url):
            raise ValueError('当前页不是允许的淘宝/天猫HTTPS页面；暂停操作')
        await self.check_page(page)
        return page

    async def check_page(self, page):
        self.require_permission()
        if not allowed_url(page.url):
            raise ValueError('当前页不是允许的淘宝/天猫HTTPS页面；暂停操作')
        if re.search(r'(?:login|passport)\.', urlparse(page.url).hostname or ''):
            raise ValueError('请在浏览器中本人扫码登录，完成后再点“我已登录，读取商品”。')
        # Only inspect visible verification UI; never solve or hide it.
        for selector in ('#baxia-dialog-content', '#nc_1_wrapper', '[data-testid="captcha"]',
                         'iframe[src*="captcha"]', 'iframe[src*="baxia"]'):
            nodes = page.locator(selector)
            for index in range(min(await nodes.count(), 20)):
                if await nodes.nth(index).is_visible():
                    raise ValueError('检测到安全验证，已暂停。请本人在浏览器完成验证后重新操作。')

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
                            'notes': '本人浏览器页面读取；数量/定金/优惠口径需核对。仅当前加载的商品行。'})
            return {'items': out, 'complete': False,
                    'warning': '只证明当前DOM已加载行；虚拟滚动/折叠/失效商品可能未包含，不自动声称全量。'}

    async def _observe(self, project: Project, expected_ids: list[str]):
        page = await self.page()
        cfg = self.config
        if not cfg.checkout_total or not cfg.checkout_rows:
            raise ValueError('必须校准结算应付金额节点与结算商品行，不能从总计/节省/定金文案猜金额')
        rows = page.locator(cfg.checkout_rows)
        actual = [await rows.nth(i).get_attribute(cfg.checkout_row_id_attribute) for i in range(await rows.count())]
        aliases = cart_line_map(project)
        expected_lines = [aliases[ident] for ident in expected_ids]
        if None in actual or sorted(actual) != sorted(expected_lines):
            raise ValueError('结算商品ID与候选组不一致；已停止，不接受这笔报价')
        if cfg.checkout_quantity:
            quantities = {aliases[x.id]:x.quantity for x in project.items}
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
            aliases = cart_line_map(project)
            expected_lines = {aliases[ident] for ident in expected_ids}
            if set(mapping) != set(aliases.values()) or not expected_lines <= mapping.keys():
                raise ValueError('购物车和项目商品集合不一致，请重新采集并核对')
            for ident, row in mapping.items():
                cb = row.locator(cfg.checkbox)
                if await cb.count() != 1:
                    raise ValueError('每行必须仅对应一个原生勾选框')
                tag = await cb.evaluate('(e)=>e.tagName.toLowerCase()+":"+e.type')
                if tag != 'input:checkbox':
                    raise ValueError('仅支持原生checkbox，不对未知组件执行猜测点击')
                await self.check_page(page)
                await cb.set_checked(ident in expected_lines, timeout=5000)
            button = page.locator(cfg.checkout_button)
            if await button.count() != 1:
                raise ValueError('结算按钮不唯一')
            label = (await button.inner_text()).strip()
            if not re.fullmatch(r'结算\s*(?:[（(]\s*\d+\s*[)）])?', label):
                raise ValueError('按钮文字不在安全白名单；只允许“结算”，绝不点击提交订单/支付/确认收货')
            # This is the only non-checkbox automated click in the adapter.
            await self.check_page(page)
            await button.click(timeout=10000)
            await page.wait_for_timeout(1500)
            return await self._observe(project, expected_ids)
