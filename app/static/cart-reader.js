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
  const presets = cfg.rows ? [cfg.rows] : ['[data-cart-item]', '#J_OrderList .item-holder', '.J_ItemBody'];
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
  if(!profile) warnings.push('未识别到受支持的购物车商品行；请确认已登录并打开购物车。不会把识别失败当成空购物车。');
  warnings.push('仅包含当前页面已加载的商品；折叠、分页、虚拟滚动中的未加载商品可能遗漏。');
  if(!groups.length) warnings.push('网页未提供可识别的淘宝分组；可先在淘宝手动进入分组，再读取当前页面。按店铺筛选不是淘宝自定义分组。');
  const loading = [...document.querySelectorAll('[aria-busy="true"], [data-cart-loading="true"]')].some(visible);
  return {items,groups,loading,active_group_id:active.length===1?active[0].id:null,profile,complete:false,errors,warnings};
}
