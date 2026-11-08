"""一键采集：从打开浏览器到取回标价，全部自动完成，不需要用户输入。

安全边界（与适配器一致）：只做「勾选商品 / 点开优惠明细 / 打开商品详情页」这三类
非交易操作；绝不点击立即购买、结算、提交订单、付款、领券。遇到验证码立即停止。
"""
from __future__ import annotations
import asyncio
from .adapters.browser import BrowserAdapter, money

# 详情页标价：优先已知类名，其次取“字号最大的纯金额叶子节点”，再用行价/参考价交叉校验。
DETAIL_JS = r"""
() => {
  const vis = e => !!e && e.getClientRects().length > 0;
  const leaves = [...document.querySelectorAll('body *')].filter(vis).filter(n => n.children.length === 0);
  const nums = [];
  for (const n of leaves) {
    const t = (n.textContent || '').trim();
    if (!/^[¥￥]?\s*\d{1,7}(\.\d{1,2})?$/.test(t)) continue;
    const st = getComputedStyle(n);
    const size = parseFloat(st.fontSize) || 0;
    const color = st.color || '';
    nums.push({ text: t.replace(/[¥￥\s]/g, ''), size, color });
  }
  nums.sort((a, b) => b.size - a.size);
  const body = (document.body.innerText || '');
  // 券后/到手价通常紧贴“券后”“到手”文案，单独取一份备选
  const afterCoupon = [...document.querySelectorAll('body *')].filter(vis).filter(n => n.children.length === 0)
    .map(n => ({ node: n, t: (n.textContent || '').trim() }))
    .filter(x => /^[¥￥]?\s*\d{1,7}(\.\d{1,2})?$/.test(x.t) && /券后|到手|预估/.test((x.node.parentElement?.innerText || '').slice(0, 40)))
    .map(x => x.t.replace(/[¥￥\s]/g, ''));
  return { url: location.href, title: document.title, login: /login|passport/.test(location.hostname),
           topPrices: nums.slice(0, 6), afterCoupon,
           flags: ['券后', '到手', '官方立减', '预售', '定金'].filter(k => body.includes(k)) };
}
"""


async def wait_login(page, browser, seconds: int = 12) -> bool:
    """最多等 seconds 秒；返回是否已登录。

    扫码是唯一必须由本人完成的一步，所以这里不抛异常：
    短等不到就返回 False，由接口回一个“需要登录”的正常状态，前端继续轮询。
    """
    waited = 0
    while waited < seconds:
        try:
            if BrowserAdapter.logged_in(page):
                return True
            await page.wait_for_timeout(1500)
        except Exception:
            return False
        waited += 1.5
    try:
        return BrowserAdapter.logged_in(page)
    except Exception:
        return False


async def wait_rows(page, seconds: int = 40) -> int:
    """等购物车商品行渲染出来（首屏是异步渲染，过早勾选会勾到 0 行）。"""
    waited = 0
    while waited < seconds:
        try:
            count = await page.locator('div.trade-cart-item-info').count()
        except Exception:
            count = 0
        if count:
            await page.wait_for_timeout(1200)
            return count
        await page.wait_for_timeout(1000)
        waited += 1
    raise ValueError('购物车商品行没有加载出来；请确认已登录并停留在购物车页面')


async def select_all_rows(page) -> int:
    """勾选所有可勾选商品行，返回勾选数。只接受购物车行内的原生 checkbox。"""
    boxes = page.locator('div.trade-cart-item-info input.ant-checkbox-input:not([disabled])')
    total = await boxes.count()
    checked = 0
    for i in range(total):
        box = boxes.nth(i)
        try:
            if not await box.is_checked():
                await box.check(timeout=3000)
        except Exception:
            continue
        if await box.is_checked():
            checked += 1
    return checked


async def read_discount_detail(page) -> str:
    """点开底部「优惠明细」浮层并取回原文，然后关闭。"""
    try:
        # 实测：可点击的是底部“优惠明细”这个文本节点本身（外层容器点不开）
        entry = page.get_by_text('优惠明细', exact=False).first
        await entry.click(timeout=5000)
        await page.wait_for_timeout(1800)
    except Exception:
        return ''
    raw = ''
    try:
        raw = await page.evaluate(r"""() => {
          const vis = e => !!e && e.getClientRects().length > 0;
          const all = [...document.querySelectorAll('body *')].filter(vis)
            .filter(e => /商品总价/.test(e.innerText || '') && /优惠共减/.test(e.innerText || ''));
          const deepest = all.filter(e => !all.some(o => o !== e && e.contains(o)))[0];
          return deepest ? (deepest.innerText || '').trim().slice(0, 2000) : '';
        }""")
    finally:
        try:
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(400)
        except Exception:
            pass
    return raw


def pick_list_price(detail: dict, row_price_cents: int | None) -> tuple[int | None, str]:
    """从详情页候选里挑标价；比行价还低的候选一律不信。"""
    candidates = []
    for item in detail.get('topPrices') or []:
        cents = money(item['text'])
        candidates.append((cents, item['size'], '详情页最大字号金额'))
    for text in detail.get('afterCoupon') or []:
        cents = money(text)
        candidates.append((cents, 0, '详情页券后/到手价'))
    floor = row_price_cents or 0
    usable = [(c, s, n) for c, s, n in candidates if c >= floor]
    if not usable:
        return None, ''
    usable.sort(key=lambda x: (-x[1], x[0]))
    cents, _, note = usable[0]
    return cents, note


async def fetch_list_price(context, product: str, sku: str, row_price_cents=None) -> tuple[int | None, str]:
    if not product:
        return None, '缺少商品ID'
    page = await context.new_page()
    try:
        url = f'https://item.taobao.com/item.htm?id={product}' + (f'&skuId={sku}' if sku and sku != 'nosku' else '')
        await page.goto(url, wait_until='domcontentloaded', timeout=45000)
        await page.wait_for_timeout(3500)
        detail = await page.evaluate(DETAIL_JS)
        if detail.get('login'):
            return None, '详情页要求登录'
        return pick_list_price(detail, row_price_cents)
    except Exception as e:
        return None, f'详情页读取失败：{type(e).__name__}'
    finally:
        try:
            await page.close()
        except Exception:
            pass


def split_line_id(line_id: str) -> tuple[str, str]:
    parts = (line_id or '').split(':')
    if len(parts) == 3 and parts[0] == 'sku':
        return parts[1], parts[2]
    return '', ''
