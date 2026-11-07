'use strict';
(() => {
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = n => n == null ? '还不能确定' : '¥' + (n / 100).toFixed(2);
  const uid = p => p + '-' + [...crypto.getRandomValues(new Uint8Array(8))].map(x => x.toString(16).padStart(2, '0')).join('');
  const token = $('meta[name="local-token"]').content;
  const S = {p:null, revision:'', fingerprint:'', demo:false, bundle:null, stage:'items', done:0, selected:new Set(), generation:0, editing:null, timer:null};
  let saving = false;
  function notify(text) { $('#message').textContent = text; $('#message').hidden = !text; }
  function cents(text, optional = false) {
    text = String(text).trim();
    if (!text && optional) return null;
    if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw Error('金额请填写数字，最多保留两位小数，例如 128.50。');
    const [a,b = ''] = text.split('.'); const value = Number(a) * 100 + Number((b + '00').slice(0,2));
    if (!Number.isSafeInteger(value) || value > 1000000000) throw Error('金额太大，请检查是否多填了数字。');
    return value;
  }
  async function api(path, method = 'GET', data) {
    const response = await fetch('/api/' + path, {method, headers:{'Content-Type':'application/json','X-Local-Token':token,...(S.revision ? {'X-Project-Revision':S.revision} : {})}, body:data === undefined ? undefined : JSON.stringify(data)});
    let result; try { result = await response.json(); } catch { throw Error('没有收到完整结果，请重新打开应用后再试。'); }
    if (!response.ok) {
      if (response.status === 409 || response.status === 428) throw Error('清单刚刚发生了变化。请刷新页面，再重新操作，避免覆盖新数据。');
      throw Error(typeof result.detail === 'string' ? result.detail : '有信息没有填对，请检查名称、金额和勾选项。');
    }
    return result;
  }
  const paid = () => new Set((S.p.payments || []).flatMap(x => x.item_ids));
  const pending = () => S.p.items.filter(x => !paid().has(x.id));
  const name = id => S.p.items.find(x => x.id === id)?.title || '商品';
  const resourceName = id => S.p.resources.find(x => x.id === id)?.name || '优惠';
  function fresh(q) {
    const now = Date.now(), captured = Date.parse(q.captured_at);
    return ['manual','checkout'].includes(q.source) && q.ledger_complete && q.context_fingerprint === S.fingerprint &&
      Number.isFinite(captured) && captured <= now && now - captured < 540000 &&
      (!q.valid_until || Date.parse(q.valid_until) > now + 60000) && !q.item_ids.some(id => paid().has(id));
  }
  async function refresh() {
    const data = await api('project');
    S.p = data.project; S.revision = data.revision; S.fingerprint = data.fingerprint;
    S.bundle = null; S.selected = new Set(pending().filter(x => x.required).map(x => x.id));
    render(); document.body.dataset.ready = 'true';
  }
  function selectedResult() {
    const c = S.bundle?.comparison;
    return c?.strategies.find(x => x.key === c.recommended_key)?.result || null;
  }
  function show(stage) {
    S.stage = stage;
    $$('.screen').forEach(x => { x.hidden = x.id !== 'screen-' + stage; });
    $$('[data-stage]').forEach(x => { x.classList.toggle('active', x.dataset.stage === stage); x.setAttribute('aria-current', x.dataset.stage === stage ? 'step' : 'false'); });
    footer();
  }
  function footer() {
    let label, hint;
    if (S.demo) { label = '退出体验，开始用自己的清单'; hint = '体验不会产生订单，也不会改动你的真实清单。'; }
    else if (!pending().length) { label = '＋ 添加商品'; hint = S.p.items.length ? '这些商品已登记完成付款。' : '第一次使用？点“一键体验”就能看到完整示例。'; }
    else if (S.stage === 'items') { label = '下一步：核对价格'; hint = '已记下 ' + pending().length + ' 件商品，接下来填淘宝实际应付金额。'; }
    else if (S.stage === 'prices') { const count = S.p.quotes.filter(fresh).length; label = count ? '帮我算怎么付更省' : '先记一组应付金额'; hint = count ? '会同时比较一起买、分开买，不重复用同一张券。' : '先记录全部一起买的金额，再增加分组价格，比较才更充分。'; }
    else { label = selectedResult() ? '更新价格，再算一次' : '去补充价格'; hint = '付款仍在淘宝完成。每付一笔，都要核对剩余优惠并重新计算。'; }
    $('#next').textContent = label; $('#next-hint').textContent = hint;
  }
  function render() {
    $('#demo-banner').hidden = !S.demo;
    $('#demo').textContent = S.demo ? '重新体验' : '一键体验';
    $('#empty').hidden = S.p.items.length > 0; $('#shopping').hidden = !S.p.items.length;
    $('#item-count').textContent = pending().length + ' 件';
    $('#items').innerHTML = S.p.items.map((x,i) => `<div class="item"><span class="item-icon" aria-hidden="true">${i+1}</span><div class="item-name"><b>${esc(x.title)}</b><small>${esc(x.sku || '未填写规格')} · ${x.quantity} 件${paid().has(x.id)?' · 已付款':!x.required?' · 可选凑单（保留）':''}</small></div><div class="item-price"><b>${x.display_price_cents == null ? '价格稍后填' : money(x.display_price_cents)}</b><small>展示价，仅供参考</small></div>${S.demo || paid().has(x.id) ? '' : `<button data-edit-item="${esc(x.id)}">修改</button>`}</div>`).join('');
    $('#price-items').innerHTML = pending().map(x => `<label class="check-row"><input type="checkbox" data-select="${esc(x.id)}" ${S.selected.has(x.id)?'checked':''}><span>${esc(x.title)}<small>${esc(x.sku)} × ${x.quantity}${!x.required?' · 可选凑单，按买后保留计算':''}</small></span></label>`).join('') || '<p class="muted">先添加要买的商品。</p>';
    $('#coupons').innerHTML = S.p.resources.map(x => `<p class="hint">${esc(x.name)} <b>剩 ${x.available}${['coupon','redpacket'].includes(x.kind)?' 张':' 单位'}</b></p>`).join('') || '<p class="muted">没有记录券或红包。用了优惠时再添加。</p>';
    const quotes = S.p.quotes.filter(q => ['manual','checkout','draft'].includes(q.source) && !q.item_ids.some(id => paid().has(id)));
    $('#quote-count').textContent = quotes.filter(fresh).length + ' 组近期价格';
    $('#quotes').innerHTML = quotes.slice(-80).reverse().map(q => `<div class="quote-row"><div><b>${esc(q.item_ids.map(name).join(' + '))}</b><small class="${fresh(q)?'':'badge-stale'}">${fresh(q)?'近期已核对 · 付款前再看一眼':'需要重新核对，暂不用于计算'}</small></div><strong>${money(q.cash_due_cents + q.extra_cost_cents)}</strong><button data-quote="${esc(q.id)}">更新价格</button></div>`).join('') || '<p class="muted">还没有记录。选中商品后，点“记下这组的应付金额”。</p>';
    show(S.stage);
  }
  function input(id, label, value = '', hint = '', type = 'text', required = false) {
    return `<label class="field">${esc(label)}<input id="f-${esc(id)}" type="${type}" value="${esc(value)}" ${required?'required':''} ${type==='text' && ['amount','price','deposit','extra'].includes(id)?'inputmode="decimal"':''}>${hint?`<small>${esc(hint)}</small>`:''}</label>`;
  }
  const check = (id, text) => `<label class="check-row"><input id="f-${esc(id)}" type="checkbox"><span>${esc(text)}</span></label>`;
  function dialog(kind, object, title, fields, submit = '保存') {
    S.editing = {kind, object}; $('#dialog-title').textContent = title; $('#fields').innerHTML = fields;
    $('#form-error').textContent = ''; $('#save').textContent = submit; $('#editor').showModal();
  }
  function editItem(id) {
    if (S.demo) return;
    const x = S.p.items.find(i => i.id === id);
    dialog('item', x, x ? '修改商品' : '添加想买的商品',
      input('title','商品叫什么？',x?.title || '', '例如：面霜 50ml、猫粮 6kg', 'text', true) +
      input('price','页面展示价（元，可不填）',x?.display_price_cents == null?'':(x.display_price_cents/100).toFixed(2), '已经扣过券或红包也没关系，这个金额只作参考。') +
      `<details><summary>规格、数量和预售定金（选填）</summary>${input('sku','规格',x?.sku || '')}${input('quantity','数量',x?.quantity || 1,'','number',true)}${input('deposit','已经付过的定金（元）',x?.deposit_paid_cents == null?'':x.deposit_paid_cents/100, '不确定可留空；后面直接填淘宝还要付的金额。')}</details>${x ? `<button type="button" class="text-button" data-remove-item="${esc(x.id)}">删除这件商品</button>` : ''}`, '保存商品');
  }
  function addCoupon() {
    dialog('coupon', null, '添加一张券或红包',input('name','给它一个好认的名称','','例如：88VIP 满1000减125（第1张）。同一张不要重复添加。','text',true) +
      '<label class="field">类型<select id="f-kind"><option value="coupon">优惠券</option><option value="redpacket">购物红包（整张使用）</option></select></label>' +
      `<details><summary>有效期（券面有写就填）</summary>${input('ends','到期时间（本机时间）','','','datetime-local')}</details>` + '<p class="form-note">添加新优惠后，之前记下的价格需要重新核对。可拆红包、金币、购物金请使用高级设置。</p>', '保存这张优惠');
  }
  function openQuote(id) {
    if (S.demo) return;
    const old = S.p.quotes.find(q => q.id === id), ids = old ? old.item_ids : [...S.selected];
    if (!ids.length) return notify('先勾选这一笔准备一起购买的商品。');
    if (old && (Object.keys(old.granted).length || old.requires_done.length || old.requires_zero.length || old.extra_cost_cents)) return notify('这条价格含返券、先买条件或额外费用，请在“更多 → 高级设置”中完整更新。');
    const eligible = S.p.resources.filter(r => r.available > 0);
    if (eligible.some(r => !['coupon','redpacket'].includes(r.kind) || r.opportunity_cost_cents || r.available !== 1)) return notify('当前清单含金币、购物金或可拆余额，请在“更多 → 高级设置”录入价格，避免漏算本金。');
    dialog('quote', {old, ids}, '记下淘宝的实际应付金额',
      `<p class="form-note">${esc(ids.map(name).join(' + '))}</p>` + input('amount','还需要支付多少元？',old?(old.cash_due_cents/100).toFixed(2):'', '填写确认订单页的应付金额，含运费；不要再减券或定金。','text',true) +
      '<h3>这一笔用了哪些券、红包？</h3>' + (eligible.map(r => `<label class="check-row"><input type="checkbox" data-used="${esc(r.id)}" ${old?.consumed[r.id]?'checked':''}><span>${esc(r.name)}</span></label>`).join('') || '<p class="hint">用了券或红包？请先取消，回到“我的券与红包”添加；没有使用就继续。</p>') +
      check('confirmed','我刚在淘宝核对了金额，已把用掉的券、红包全部选上（没有就不选）。') +
      `<details><summary>用于分开付款时，再核对这一项</summary>${check('independent','我已核对：用掉未选中的其他优惠后，本组金额仍然不变。')}<p class="hint">不确定就不勾选。我们不会假定几组单独看到的低价能同时享受。</p></details>`, '保存这组价格');
  }
  function openPayment(index) {
    const r = selectedResult(); if (!r || index !== 0) return;
    const s = r.steps[index];
    if (!validPlan()) { S.bundle = null; renderResult(); return notify('价格或清单已经变化，请重新核对再计算。'); }
    const rows = (map,prefix,label) => Object.entries(map).map(([id,n]) => input(prefix+id,label+resourceName(id),n,'按实际结果填写，券一般为 1；没有使用或未到账填 0。','number',true)).join('');
    dialog('payment', {...s, record_id:uid('paid')}, '确认你已经在淘宝付完这一笔',`<p class="form-note">这里只做记录，不会扣款。商品：${esc(s.item_ids.map(name).join(' + '))}</p>`+
      input('amount','淘宝实际支付金额（元）',s.checkout_cash_due_cents/100,'以支付成功页面为准，不包含以前已付定金。','text',true)+
      (s.extra_cash_cost_cents?input('extra','其他已支付费用（元）',s.extra_cash_cost_cents/100):'') +
      rows(s.consumed,'spent-','实际用掉：')+rows(s.granted,'earned-','实际已获得：')+
      check('paid','我已完成真实付款，并核对了金额及全部优惠变化。'), '确认已付款，并更新剩余清单');
  }
  function validPlan() {
    if (S.demo) return true;
    if (!S.bundle || S.bundle.project_revision !== S.revision || S.bundle.project_changed_during_search) return false;
    const r = selectedResult(); return !!r && r.steps.every(s => !s.effective_deadline || Date.parse(s.effective_deadline) > Date.now()+60000);
  }
  async function calculate(demo) {
    if (!demo && !pending().some(x => x.required)) return notify('先添加这次要买的商品。');
    const generation = ++S.generation;
    $('#busy-title').textContent = demo ? '正在为你算好体验示例' : '正在比较怎么付款更合适'; $('#busy').hidden = false; notify('');
    try {
      const data = await api('simple/' + (demo ? 'demo' : 'plan'), 'POST', {});
      if (generation !== S.generation) return;
      if (data.project_changed_during_search) throw Error('计算时清单有了新变化，请刷新后再算一次。');
      if (demo) { S.demo = true; S.p = data.project; S.done = 0; }
      else { S.demo = false; S.p = data.project; S.revision = data.project_revision; S.fingerprint = data.fingerprint; }
      S.bundle = data; render(); renderResult(); show('result'); $('#result-title').setAttribute('tabindex','-1'); $('#result-title').focus();
    } finally { if (generation === S.generation) $('#busy').hidden = true; }
  }
  function renderResult() {
    const r = selectedResult();
    $('#result-title').textContent = S.demo ? '示例算好了，照着这两笔买' : '这次可以这样付款';
    $('#result-subtitle').textContent = S.demo ? '不用配券、不用选模式。下面的金额仅用于演示，不是你的真实订单。' : '金额来自已核对的价格。每付完一笔，都会提醒你更新剩余优惠。';
    if (!r || !validPlan()) {
      const missing = S.bundle?.comparison?.strategies?.find(x => x.key==='optimized')?.result?.missing_item_ids || [];
      $('#result').innerHTML = `<div class="no-result"><h2>${S.bundle?'还不能确定最省的付款方式':'先记下价格，再帮你比较'}</h2><p>${missing.length?'这些商品还缺近期价格：'+esc(missing.map(name).join('、')):'可能是价格过期、同一张券被多组占用，或还没有记录完整的购买组合。'}</p><p>最简单的下一步：把全部要买的商品一起勾选，记一组当前应付金额。</p><button data-action="prices" class="primary">去核对价格</button></div>`; footer(); return;
    }
    const single = S.bundle.comparison.strategies.find(x => x.key==='single')?.result;
    const merged = S.bundle.comparison.strategies.find(x => x.key==='merged')?.result;
    const saving = single?.economic_cost_cents != null ? single.economic_cost_cents - r.economic_cost_cents : null;
    const prefix = S.demo ? '示例' : '按已记录价格';
    $('#result').innerHTML = `<div class="result-hero"><div><span class="pill">${prefix} · 分 ${r.order_count} 笔付款</span><p class="hint">还需要支付</p><div class="result-money">${money(r.cash_due_cents)}</div>${saving != null && saving > 0?`<div class="save-line">${S.demo?'示例中，':''}比逐件付款少花 <b>${money(saving)}</b></div>`:'<p class="hint">'+(saving===0?'与逐件付款相比，没有发现额外节省。':'还缺逐件付款对照，不虚报节省金额。')+'</p>'}${r.economic_cost_cents!==r.cash_due_cents?`<p class="hint">计入已花的购物金等本金：${money(r.economic_cost_cents)}</p>`:''}</div><div class="compare"><div><small>一件件付款</small><b>${money(single?.economic_cost_cents)}</b></div><div><small>全部一起付</small><b>${money(merged?.economic_cost_cents)}</b></div></div></div>` +
      r.steps.map((s,i) => `<article class="payment ${S.demo && i<S.done?'done':''}"><span class="step-num">${S.demo && i<S.done?'✓':i+1}</span><div class="payment-body"><h3><span>第 ${i+1} 笔，一起买这 ${s.item_ids.length} 件</span><b>${money(s.cash_due_cents)}</b></h3><p>${esc(s.item_ids.map(name).join(' + '))}</p><small>使用优惠：${esc(Object.entries(s.consumed).filter(([,v])=>v).map(([id,v])=>resourceName(id)+(v===1?'':' × '+v)).join('、') || '无单独券或红包')}</small>${S.demo?(i<S.done?'<span class="pill">示例已走完这一步，没有真实付款</span>':`<button class="${i===S.done?'primary':''}" data-demo-step="${i}" ${i!==S.done?'disabled':''}>${i===S.done?'模拟完成这一笔':'先完成上一笔'}</button>`):`<small>${i===0?'请先在淘宝核对应付金额，再自行付款。':'先完成第一笔，再更新价格；本笔金额可能变化。'}${Date.parse(s.earliest_start)>Date.now()+60000?' 可开始时间：'+esc(new Date(s.earliest_start).toLocaleString('zh-CN')):''}${s.effective_deadline?' 有效至：'+esc(new Date(s.effective_deadline).toLocaleTimeString('zh-CN')):''}</small>${i===0?'<button class="primary" data-paid="0">我已在淘宝付完这一笔</button>':''}`}</div></article>`).join('') +
      (S.demo && S.done===r.steps.length?'<div class="success"><h2>体验完成，就是这么简单</h2><p>正式使用时，按每笔商品组合到淘宝付款。你刚才没有产生任何订单。</p><button data-action="exit" class="primary">开始用自己的清单</button></div>':'') +
      `<p class="result-note">${S.demo?'示例完全虚构，只展示操作过程。':'只在本次已记录的有效价格中比较，不保证覆盖所有活动或入口。'+(r.status==='best_found'?'计算时间已到，当前展示的是已找到的最低方案，尚未证明最优。':'')} 已付定金不再重复扣除；凑单商品按实际保留计算，不假定退款后还能保留优惠。</p><div class="result-tools"><button data-action="export-plan">导出这份步骤清单</button><button data-action="${S.demo?'retry':'prices'}">${S.demo?'再体验一次':'补充分组价格，继续比较'}</button></div>`;
    footer();
  }
  async function exitDemo() { ++S.generation; S.demo = false; S.bundle = null; S.done = 0; S.stage = 'items'; notify(''); await refresh(); }
  function download(text, filename, type = 'application/json') { const url=URL.createObjectURL(new Blob([text],{type})); const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
  function exportPlan() {
    const r = selectedResult(); if (!r || !validPlan()) return notify('请先计算一份当前有效的方案。');
    const lines = [S.demo?'体验示例（不是实际订单）':'付款步骤（付款前请在淘宝重新核价）','还需要支付：'+money(r.cash_due_cents),''];
    r.steps.forEach((s,i)=>lines.push(`第 ${i+1} 笔：${money(s.cash_due_cents)}`,s.item_ids.map(name).join(' + '),'使用：'+Object.entries(s.consumed).map(([id,v])=>resourceName(id)+' × '+v).join('、'),''));
    lines.push('仅比较已记录的价格。每付完一笔，要更新优惠和剩余价格。不会自动下单或付款。');
    download(lines.join('\n'),S.demo?'体验示例-付款步骤.txt':'我的付款步骤.txt','text/plain;charset=utf-8');
  }
  async function submit(event) {
    event.preventDefault(); if(saving || S.demo) return;
    saving=true; $('#save').disabled=true; $('#form-error').textContent='';
    const {kind,object} = S.editing; const value = id => $('#f-'+id)?.value.trim() || '';
    try {
      if(kind==='item') {
        const quantity=Number(value('quantity')); if(!Number.isInteger(quantity)||quantity<1||quantity>999)throw Error('数量请填 1 到 999 的整数。');
        const p=structuredClone(S.p), id=object?.id||uid('item'), amount=cents(value('price'),true), deposit=cents(value('deposit'),true);
        const x={...object,id,title:value('title'),sku:value('sku'),quantity,display_price_cents:amount,display_price_kind:object?.display_price_kind||'unknown',deposit_paid_cents:deposit,deposit_confirmed:deposit===null?false:(object?.deposit_confirmed||false)};
        if(!x.title)throw Error('先填一个商品名称，方便你识别。');
        // Editing basics invalidates old rule assumptions rather than trusting a stale base.
        if(object && (x.quantity!==object.quantity || x.sku!==object.sku || x.display_price_cents!==object.display_price_cents)) { x.pricing_base_cents=null;x.pricing_base_evidence=''; }
        p.items=p.items.filter(i=>i.id!==id).concat(x); await api('project','PUT',p);
      } else if(kind==='coupon') {
        const p=structuredClone(S.p), title=value('name'); if(!title)throw Error('请给这张优惠取个名称。');
        if(p.resources.some(r=>r.name===title))throw Error('已经记过这个名称。是同一张就不用再添加；是另一张请在名称后加“第2张”。');
        p.resources.push({id:uid('coupon'),name:title,kind:value('kind'),available:1,ends_at:value('ends')?new Date(value('ends')).toISOString():null}); await api('project','PUT',p);
      } else if(kind==='quote') {
        if(!$('#f-confirmed').checked)throw Error('请核对淘宝金额及优惠后，勾选确认项。');
        const now=new Date(), consumed={}; $$('[data-used]:checked').forEach(x=>{consumed[x.dataset.used]=1;});
        const quote={id:object.old?.id||uid('quote'),item_ids:object.ids,cash_due_cents:cents(value('amount')),consumed,source:'manual',ledger_complete:true,portable_state:$('#f-independent').checked,observed_balances:Object.fromEntries(S.p.resources.map(r=>[r.id,r.available])),captured_at:now.toISOString(),valid_until:new Date(now.getTime()+600000).toISOString(),context_fingerprint:S.fingerprint,evidence_note:'本人刚在淘宝确认订单页核对仍需支付金额（含运费），并逐项核对所用券和红包。'};
        await api('quotes','POST',{quote,expected_fingerprint:S.fingerprint,checkout_observed:true});
      } else if(kind==='payment') {
        if(!$('#f-paid').checked)throw Error('只有在淘宝已经付款成功，才能勾选并保存。');
        const units = prefix => {const result={};for(const r of S.p.resources){const e=document.getElementById('f-'+prefix+r.id);if(e){const n=Number(e.value);if(!Number.isSafeInteger(n)||n<0||n>1000000000)throw Error('优惠数量请填写非负整数。');if(n)result[r.id]=n;}}return result;};
        await api('payments/record','POST',{actual_payment_confirmed:true,record:{id:object.record_id,quote_id:object.quote_id,item_ids:object.item_ids,cash_paid_cents:cents(value('amount')),extra_cost_cents:value('extra')?cents(value('extra')):0,consumed:units('spent-'),granted:units('earned-'),paid_at:new Date().toISOString(),evidence_note:'本人确认淘宝付款成功，并核对实际金额和优惠变化；这是手工记录，不是自动付款。'}});
      }
      $('#editor').close(); await refresh();
      if(kind==='payment'){show(pending().length?'prices':'items');notify('这一笔已经记好。剩余优惠已更新，请在淘宝重新核对剩下商品的价格。');}
      else notify(kind==='quote'?'价格已记好，可以继续加一组对照，或直接计算。':kind==='coupon'?'优惠已保存。之前的价格需要重新核对，避免用错券。':'商品已保存。');
    } catch(e) { $('#form-error').textContent=e.message; } finally { saving=false;$('#save').disabled=false; }
  }
  function action(fn) { return (...args) => Promise.resolve().then(()=>fn(...args)).catch(e=>notify(e.message)); }
  $('#edit-form').addEventListener('submit',submit); $('#close-editor').onclick=()=>{if(!saving)$('#editor').close();};
  $('#editor').addEventListener('cancel',e=>{if(saving)e.preventDefault();});
  ['demo','demo-first'].forEach(id=>$('#'+id).onclick=action(()=>calculate(true)));
  $('#exit-demo').onclick=action(exitDemo);
  ['add-first','add-item'].forEach(id=>$('#'+id).onclick=()=>editItem());
  $('#add-coupon').onclick=addCoupon; $('#add-quote').onclick=()=>openQuote();
  $('#select-all').onclick=()=>{S.selected=new Set(pending().map(x=>x.id));render();};
  $('#stop-view').onclick=()=>{++S.generation;$('#busy').hidden=true;notify('已返回。未修改你的清单，也没有产生订单。');};
  $('#next').onclick=action(()=>{if(S.demo)return exitDemo();if(!pending().length)return editItem();if(S.stage==='items')return show('prices');if(S.stage==='prices')return S.p.quotes.some(fresh)?calculate(false):openQuote();return show('prices');});
  $$('[data-stage]').forEach(b=>b.onclick=()=>{if(S.demo)return notify('体验已经自动算好，直接按下方步骤试一遍即可。');if(b.dataset.stage==='result')renderResult();show(b.dataset.stage);});
  document.addEventListener('change',e=>{const id=e.target.dataset.select;if(id){if(e.target.checked)S.selected.add(id);else S.selected.delete(id);}});
  document.addEventListener('click',action(async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.removeItem) {
      if(saving || S.demo) return;
      if(!confirm('删除这件商品及它的价格记录、专用规则？不会取消淘宝订单。')) return;
      const id=b.dataset.removeItem, p=structuredClone(S.p);
      if(paid().has(id)) return notify('已付款的商品不能在这里删除。');
      p.items=p.items.filter(x=>x.id!==id);
      p.quotes=p.quotes.filter(q=>!q.item_ids.includes(id)&&!q.requires_done.includes(id));
      p.rules=p.rules.filter(r=>!r.item_ids.includes(id));
      p.candidate_groups=p.candidate_groups.filter(g=>!g.includes(id));
      await api('project','PUT',p);$('#editor').close();await refresh();notify('商品已从本地清单移除。');return;
    }
    if(b.dataset.editItem)return editItem(b.dataset.editItem);
    if(b.dataset.quote)return openQuote(b.dataset.quote);
    if(b.dataset.paid!==undefined)return openPayment(Number(b.dataset.paid));
    if(b.dataset.demoStep!==undefined && S.demo && Number(b.dataset.demoStep)===S.done){S.done++;renderResult();return;}
    const a=b.dataset.action;if(a==='prices'){show('prices');return;}
    if(a==='exit')return exitDemo();if(a==='retry')return calculate(true);if(a==='export-plan')return exportPlan();
  }));
  $('#export').onclick=action(async()=>{const data=await api('project');download(JSON.stringify(data.project,null,2),'我的购物清单.json');$('#more').open=false;});
  $('#import-button').onclick=()=>{if(S.demo)return notify('请先退出体验，再导入自己的清单。');$('#import-file').click();};
  $('#import-file').onchange=action(async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>12000000)throw Error('清单文件超过12MB，请检查文件是否正确。');if(S.p.items.length&&!confirm('导入会替换当前清单（会自动备份）。确定继续？'))return;await api('project','PUT',JSON.parse(await f.text()));S.stage='items';await refresh();notify('清单已导入。旧价格需要重新核对。');}finally{e.target.value='';$('#more').open=false;}});
  document.addEventListener('visibilitychange',action(async()=>{if(document.hidden||S.demo||!S.p)return;const d=await api('project');if(d.revision!==S.revision){if($('#editor').open){notify('另一处已修改清单；请先关闭填写窗口，再刷新。');return;}await refresh();renderResult();notify('已同步最新清单，请重新计算付款方案。');}}));
  setInterval(()=>{if(!S.demo && S.bundle && !validPlan()){S.bundle=null;renderResult();notify('这份方案的价格已接近过期，请到淘宝重新核对后再计算。');}},10000);
  refresh().then(()=>{if(new URLSearchParams(location.search).get('demo')==='1')return calculate(true);}).catch(e=>{notify(e.message);$('#next').disabled=true;});
})();
