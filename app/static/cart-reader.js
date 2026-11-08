/* Read rendered cart information only. No requests, cookies, storage, or clicks. */
(cfg) => {
  const visible = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const text = e => e?.textContent?.trim().replace(/\s+/g,' ') || '';
  const first = (root, selectors) => {
    for (const selector of selectors.filter(Boolean)) {
      const nodes = [...root.querySelectorAll(selector)].filter(visible);
      if (nodes.length === 1) return nodes[0];
    }
    return null;
  };
  const attr = (e, names) => names.map(n=>e.getAttribute(n)).find(Boolean) || '';
  const safeLink = a => {
    try { const u = new URL(a?.href || '', location.href);
      return u.protocol==='https:' && !u.username && !u.password &&
        /(^|\.)(taobao\.com|tmall\.com|tmall\.hk)$/.test(u.hostname) ? u.href : '';
    } catch { return ''; }
  };
  // 2026-11 真实购物车：行 .trade-cart-item-info，店铺 .trade-cart-shop-container(#bundle_s_<sellerId>)。
  // 价格由整数/小数/小数点三个节点拼成；带“平台加补后”等标签时，第二个容器才是参考价。
  const NEW_PROFILE = 'div.trade-cart-item-info';
  const readPriceBox = box => {
    const int = box.querySelector('[class*="trade-price-integer"]');
    if (!int) return '';
    const dec = box.querySelector('[class*="trade-price-decimal"]');
    return `${text(int)}${dec ? '.' + text(dec) : ''}`;
  };
  const presets = cfg.rows ? [cfg.rows] : ['[data-cart-item]', NEW_PROFILE, '#J_OrderList .item-holder', '.J_ItemBody'];
  let rows = [], profile = '';
  for (const selector of presets) {
    const candidates = [...document.querySelectorAll(selector)].filter(visible);
    if (candidates.length) {rows=candidates;profile=selector;break;}
  }
  const tabs = [...document.querySelectorAll(cfg.group_tabs || '[role="tab"][data-group-id], [data-cart-group-id][role="tab"]')].filter(visible);
  const groups = tabs.map(e=>({id:attr(e,['data-cart-group-id','data-group-id']), name:text(e),
    active:e.getAttribute('aria-selected')==='true' || e.getAttribute('data-active')==='true', source:'taobao'}))
    .filter(g=>g.id && g.name);
  const active = groups.filter(g=>g.active);
  const errors = [], warnings = [], items = [], seen = new Set();
  if(rows.length>500) errors.push('当前页面超过500行，请先在淘宝选择一个分组。');
  for (const row of rows.slice(0,500)) {
    if (profile === NEW_PROFILE) {
      const itemLinks = [...row.querySelectorAll('a[href*="item.htm"]')].filter(visible);
      const link = itemLinks.sort((a,b)=>(b.innerText||'').trim().length-(a.innerText||'').trim().length)[0] || null;
      const rowTitle = (link?.innerText || '').trim().replace(/\s+/g,' ');
      let product = '', skuId = '';
      try { const u = new URL(link?.href || '', location.href);
        product = u.searchParams.get('id') || ''; skuId = u.searchParams.get('skuId') || ''; } catch {}
      const rowLine = product ? `sku:${product}:${skuId || 'nosku'}` : '';
      if (!rowTitle || !rowLine) { errors.push('有商品缺少唯一行标识或标题，未导入；不能只凭商品名合并。'); continue; }
      if (seen.has(rowLine)) { errors.push('发现重复商品行标识，可能是页面结构变化；请切换分组后重新读取。'); continue; }
      seen.add(rowLine);
      const boxes = [...row.querySelectorAll('[class*="trade-price-container"]')].filter(visible);
      const priceText = boxes[0] ? readPriceBox(boxes[0]) : '';
      const priceLabel = boxes[0] ? text(boxes[0].querySelector('[class*="trade-price-label"]')) : '';
      const refText = boxes[1] ? readPriceBox(boxes[1]) : '';
      const qtyInput = row.querySelector('[class*="trade-cart-item-quantity"] input, input[class*="inputWrapper"]');
      const qtyNode = [...row.querySelectorAll('[class*="quantityNum"]')].filter(visible)[0] || null;
      const qtyText = qtyInput ? String(qtyInput.value || '').trim() : (qtyNode ? text(qtyNode) : '');
      const rowQty = /^[1-9]\d{0,2}$/.test(qtyText) ? Number(qtyText) : null;
      const shopBox = row.closest('[class*="trade-cart-shop-container"]') || row;
      const rowShopId = (shopBox.getAttribute('id') || '').replace(/^bundle_s_/, '');
      const rowShopName = text(shopBox.querySelector('[class*="shopName"], [class*="shop-name"]'));
      const promos = (row.innerText || '').split(/\n+/).map(s=>s.trim())
        .filter(t=>t && t.length<=40 && /官方立减|消费券|补贴|礼金|满减|券|直降/.test(t)).slice(0,10);
      const rowBox = row.querySelector('input[type=checkbox]');
      items.push({line_id: rowLine, product_id: product, title: rowTitle.slice(0,400), sku: '', quantity: rowQty,
        shop_id: rowShopId, shop_name: rowShopName.slice(0,160),
        display_price_text: priceText.slice(0,80), price_scope: priceText ? 'unit' : 'unknown',
        price_label: priceLabel, reference_price_text: refText.slice(0,80),
        group_ids: [], group_names: [], observed_promotions: promos,
        selected: !!rowBox?.checked,
        // 原生 checkbox 在多数可购买行上也带 disabled（渲染态），不能据此判断不可买；
        // 只按页面明示的失效文案判断。
        disabled: /商品缺货|不可购买|补货中|已失效|已下架/.test((row.innerText||'').slice(0,160))});
      continue;
    }
    const titleNode=first(row,[cfg.title,'[data-title]','.item-basic-info a.item-title','a.item-title','a[href*="item.taobao.com/item.htm"]','a[href*="detail.tmall.com/item.htm"]']);
    const title=titleNode?.getAttribute('data-title') || text(titleNode);
    const itemURL=safeLink(titleNode?.closest('a') || row.querySelector('a[href*="item.htm"]'));
    let product=attr(row,['data-item-id','data-itemid']);
    if(!product && itemURL) product=new URL(itemURL).searchParams.get('id') || '';
    const skuNode=first(row,[cfg.sku,'[data-sku-text]','.item-props']);
    const sku=skuNode?.getAttribute('data-sku-text') || text(skuNode);
    const skuId=attr(row,['data-sku-id','data-skuid']);
    const cartId=attr(row,[cfg.row_id_attribute || 'data-cart-id','data-cart-id','data-cartid','data-id']);
    const line=cartId || (product && (skuId||sku) ? `sku:${product}:${skuId||sku}` : '');
    if(!title || !line) {errors.push('有商品缺少唯一行标识或标题，未导入；不能只凭商品名合并。');continue;}
    if(seen.has(line)) {errors.push('发现重复商品行标识，可能是页面结构变化；请切换分组后重新读取。');continue;}
    seen.add(line);
    const q=first(row,[cfg.quantity,'[data-quantity]','input.J_ItemAmount','input[type="number"]']);
    const quantityText=q?.getAttribute('data-quantity') || q?.value || text(q);
    const quantity=/^[1-9]\d{0,2}$/.test(quantityText) ? Number(quantityText) : null;
    const priceNode=first(row,[cfg.display_price,'[data-display-price]','.price-now .J_Price']);
    const priceText=priceNode?.getAttribute('data-display-price') || text(priceNode);
    const scope=attr(priceNode || row,['data-price-scope']) || (profile==='#J_OrderList .item-holder'||profile==='.J_ItemBody'?'unit':'unknown');
    const shop=row.closest('[data-shop-id],.J_Order,.order-holder') || row;
    const shopNode=first(shop,[cfg.shop_name,'[data-shop-name]','.shop-info .shop-name','.shop-info a']);
    const shopName=shop.getAttribute('data-shop-name') || shopNode?.getAttribute('data-shop-name') || text(shopNode);
    const shopId=attr(shop,['data-shop-id','data-seller-id','data-sellerid']);
    const promotion=[...row.querySelectorAll('[data-promotion],.promotion-info,.item-promo')].filter(visible).map(text).filter(Boolean).slice(0,10);
    items.push({line_id:line,product_id:product,title:title.slice(0,400),sku:sku.slice(0,400),quantity,
      shop_id:shopId,shop_name:shopName.slice(0,160),display_price_text:priceText.slice(0,80),price_scope:scope,
      group_ids:active.length===1?[active[0].id]:[],group_names:active.length===1?[active[0].name]:[],
      observed_promotions:promotion,disabled:row.getAttribute('data-disabled')==='true'||row.getAttribute('aria-disabled')==='true'});
  }
  // 底部合计：totalPrice 是拆分节点，需先去掉空白再取金额
  const moneyIn = raw => { const m = String(raw||'').replace(/\s/g,'').match(/(\d{1,7}(?:\.\d{1,2})?)/); return m ? m[1] : ''; };
  const totalNode = [...document.querySelectorAll('[class*="totalPrice"]')].filter(visible)[0] || null;
  const discNode = [...document.querySelectorAll('[class*="totalDiscount"]')].filter(visible)[0] || null;
  const summary = totalNode ? {
    payable_text: moneyIn(text(totalNode)),
    discount_text: discNode ? moneyIn(text(discNode)) : '',
    raw: text(totalNode).slice(0,120),
    discount_raw: discNode ? text(discNode).slice(0,120) : ''
  } : null;
  // 优惠明细：只有在你手动点开浮层后才会读到；读取器本身从不点击
  const dlgAll = [...document.querySelectorAll('body *')].filter(visible)
    .filter(e => /商品总价/.test(e.innerText||'') && /优惠共减/.test(e.innerText||''));
  const dlg = dlgAll.filter(e => !dlgAll.some(o => o !== e && e.contains(o)))[0] || null;
  const detail_raw = dlg ? (dlg.innerText||'').trim().slice(0,2000) : '';
  if(!profile) warnings.push('未识别到受支持的购物车商品行；请确认已登录并打开购物车。不会把识别失败当成空购物车。');
  if(!summary) warnings.push('未读到购物车底部合计节点；请确认已勾选商品。');
  warnings.push('仅包含当前页面已加载的商品；折叠、分页、虚拟滚动中的未加载商品可能遗漏。');
  if(!groups.length) warnings.push('网页未提供可识别的淘宝分组；可先在淘宝手动进入分组，再读取当前页面。按店铺筛选不是淘宝自定义分组。');
  const loading = [...document.querySelectorAll('[aria-busy="true"], [data-cart-loading="true"]')].some(visible);
  return {items,groups,loading,active_group_id:active.length===1?active[0].id:null,profile,complete:false,errors,warnings,summary,detail_raw};
}
