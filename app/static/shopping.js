'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="local-token"]').content;
  let project = {items:[],rules:[]}, revision='', snapshot=null, demo=false, demoData=null, selection=new Set(), currentGroup='view:current', editingBase=null;
  const money = value => value == null ? '待核对' : '¥'+(value/100).toFixed(2);
  const el = (tag,text,cls) => {const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  function message(text,ok=false) {$('message').textContent=text;$('message').hidden=!text;$('message').style.color=ok?'#287d5b':'';}
  async function api(path,options={}) {
    const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Local-Token':token,'X-Project-Revision':revision,...options.headers}});
    const body=await r.json();if(!r.ok)throw new Error(typeof body.detail==='string'?body.detail:'信息格式有误，请检查输入。');return body;
  }
  async function busy(title,fn) {
    if(!$('loading').hidden)return;
    $('loading-text').textContent=title;$('loading').hidden=false;message('');
    try{return await fn();}catch(error){message(error.message);}finally{$('loading').hidden=true;}
  }
  async function loadProject(){const result=await api('/api/project');project=result.project;revision=result.revision;renderOffers();}
  function tab(name){['cart','offers','plan'].forEach(k=>$(k).hidden=k!==name);document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));history.replaceState(null,'','#'+name);}
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
  function checkbox(label,value,checked=false){const l=el('label',null,'check'),c=el('input');c.type='checkbox';c.value=value;c.checked=checked;l.append(c,el('span',label));return l;}
  const ids=id=>[...$(id).querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);
  function cents(raw,empty=false){const s=String(raw).trim();if(!s&&empty)return null;if(!/^\d+(\.\d{1,2})?$/.test(s))throw new Error('金额请填写非负数字，最多两位小数。');const [a,b='']=s.split('.');const v=Number(a)*100+Number(b.padEnd(2,'0'));if(!Number.isSafeInteger(v)||v>1e9)throw new Error('金额超出支持范围');return v;}
  function int(id){const raw=$(id).value;if(!/^\d+$/.test(raw)||Number(raw)<1)throw new Error('件数和张数必须是正整数');return Number(raw);}
  function selectedRows(){
    if(!snapshot)return [];
    if(currentGroup.startsWith('shop:'))return snapshot.items.filter(r=>r.shop_id===currentGroup.slice(5));
    if(currentGroup.startsWith('taobao:'))return snapshot.items.filter(r=>currentGroup==='taobao:all'&&demo || r.group_ids?.includes(currentGroup.slice(7)));
    return snapshot.items;
  }
  function renderCart(){
    $('cart-items').replaceChildren();$('cart-group').replaceChildren();
    if(!snapshot){selection.clear();$('import-cart').disabled=true;$('selected-count').textContent='';$('cart-group').append(new Option('尚未读取购物车','view:current'));$('cart-items').append(el('div','还没有读取商品。也可以先点击“一键体验”。','empty'));return;}
    for(const g of snapshot.groups){const prefix={taobao:demo?'示例分组：':'淘宝分组：',shop:'按店铺：',view:''}[g.source]||'';$('cart-group').append(new Option(prefix+g.name,g.id));}
    if(!snapshot.groups.some(g=>g.id===currentGroup))currentGroup='view:current';$('cart-group').value=currentGroup;
    const rows=selectedRows();
    for(const r of rows){
      const line=el('div',null,'item'),check=el('input');check.type='checkbox';check.checked=selection.has(r.line_id);check.disabled=!r.can_import;check.setAttribute('aria-label','选择'+r.title);
      check.addEventListener('change',()=>{if(check.checked)selection.add(r.line_id);else selection.delete(r.line_id);count();});
      const body=el('div',null,'body');body.append(el('b',r.title),el('small',`${r.shop_name||'店铺待确认'} · ${r.sku||'规格待核对'} · ${r.quantity??'?'}件`));
      if(!r.can_import)body.append(el('small','数量未识别或商品失效，暂不能导入','failed'));
      for(const text of r.observed_promotions||[])body.append(el('small',text));
      line.append(check,body,el('span',money(r.display_price_cents),'price'));$('cart-items').append(line);
    }
    if(!rows.length)$('cart-items').append(el('div','这个分组目前没有已读取的商品。不会用全部商品替代。','empty'));
    const warnings=[...(snapshot.errors||[]),...(snapshot.warnings||[])];$('capture-note').textContent=`本次页面读取 ${snapshot.items.length} 行，当前筛选 ${rows.length} 行。`+(warnings.length?' '+warnings.join(' '):'');
    count();
  }
  function count(){const n=selectedRows().filter(r=>selection.has(r.line_id)).length;$('selected-count').textContent=`已选 ${n} 行（一次最多导入28行）`;$('import-cart').disabled=demo||!n||!!snapshot?.errors?.length;}
  $('select-cart').addEventListener('click',()=>{selectedRows().filter(r=>r.can_import).forEach(r=>selection.add(r.line_id));renderCart();});
  $('cart-group').addEventListener('change',()=>busy('正在切换购物车分组',async()=>{
    const requested=$('cart-group').value,old=currentGroup;selection.clear();
    try{
      if(requested.startsWith('taobao:')&&!demo){snapshot=await api('/api/cart/read',{method:'POST',body:JSON.stringify({native_group_id:requested.slice(7)})});}
      currentGroup=requested;renderCart();
    }catch(e){snapshot=null;selection.clear();currentGroup='view:current';renderCart();throw e;}
  }));
  $('connect').addEventListener('click',()=>busy('正在打开你自己的浏览器',async()=>{
    if(demo)throw new Error('请先退出体验，再连接真实账号。');
    if(!$('consent').checked){
      if(!window.confirm('允许在本机打开浏览器，读取你本人的淘宝购物车吗？\n不接收密码，不提交订单或付款。'))return;
      $('consent').checked=true;
    }
    await api('/api/cart/connect',{method:'POST',body:JSON.stringify({local_consent:true,new_account:$('new-account').checked})});
    $('new-account').checked=false;
    snapshot=null;selection.clear();renderCart();
    $('connection-note').textContent='浏览器已经打开。请本人扫码登录，再点击“我已登录，读取商品”。';
  }));
  $('open-cart').addEventListener('click',()=>busy('正在打开购物车',async()=>{if(demo)throw new Error('体验模式不会连接淘宝。');await api('/api/cart/open',{method:'POST',body:'{}'});}));
  $('read-cart').addEventListener('click',()=>busy('正在读取当前购物车页面',async()=>{if(demo)throw new Error('体验模式使用虚构购物车。');snapshot=null;selection.clear();renderCart();snapshot=await api('/api/cart/read',{method:'POST',body:'{}'});selection.clear();currentGroup='view:current';renderCart();}));
  $('disconnect').addEventListener('click',()=>busy('正在断开连接',async()=>{if(demo)throw new Error('体验模式没有连接淘宝。');const r=await api('/api/cart/disconnect',{method:'POST',body:'{}'});snapshot=null;selection.clear();$('consent').checked=false;renderCart();$('connection-note').textContent=r.note;}));
  $('import-cart').addEventListener('click',()=>busy('正在导入选中的商品',async()=>{
    if(demo)throw new Error('示例不会导入真实清单。');
    if(!$('account-confirmed').checked)throw new Error('请先核对账号、规格和数量并勾选确认。');
    const chosen=selectedRows().filter(r=>selection.has(r.line_id)).map(r=>r.line_id);
    await api('/api/cart/import',{method:'POST',body:JSON.stringify({snapshot_id:snapshot.snapshot_id,line_ids:chosen,group_id:currentGroup,account_confirmed:true})});
    await loadProject();tab('offers');message('商品已导入。展示价没有被当成优惠前货款，请在下方核对金额。',true);
  }));
  function renderOffers(){
    $('base-items').replaceChildren();$('offer-list').replaceChildren();
    for(const item of project.items){const line=el('div',null,'item'),body=el('div',null,'body');body.append(el('b',item.title),el('small',`${item.shop_name||item.shop_id||'店铺待确认'} · ${item.quantity}件`));
      const valid=item.pricing_base_cents!=null&&item.deposit_confirmed&&item.pricing_base_evidence;
      body.append(el('small',valid?`优惠前货款 ${money(item.pricing_base_cents)} · 已付定金 ${money(item.deposit_paid_cents)}`:'优惠前货款或定金还未核对',valid?'good':'failed'));
      const b=el('button',valid?'修改金额':'核对金额');b.disabled=demo;b.addEventListener('click',()=>openBase(item));line.append(body,b);$('base-items').append(line);}
    if(!project.items.length)$('base-items').append(el('div','先导入购物车，或返回首页添加商品。','empty'));
    for(const r of project.rules){const line=el('div',null,'rule-row'),body=el('div');body.append(el('b',r.name));const scope=r.item_ids.length?`${r.item_ids.length}种指定商品`:r.shop_ids.length?'指定店铺商品':'全部适用商品';
      body.append(el('small',`${scope} · ${Object.keys(r.required_item_quantities||{}).length?'必须搭配购买 · ':''}${r.selection_mode==='best'?'同组优惠自动择优':'按规则叠加/互斥'} · ${r.confirmed?'已核对':'待核对'}`));
      const remove=el('button','移除');remove.disabled=demo;remove.addEventListener('click',()=>busy('正在移除优惠',async()=>{await api('/api/promotions/'+encodeURIComponent(r.id),{method:'DELETE'});await loadProject();}));line.append(body,remove);$('offer-list').append(line);}
    if(!project.rules.length)$('offer-list').append(el('div','还没有录入店铺或组合优惠。点击“添加优惠”。','empty'));
    $('add-offer').disabled=demo||!project.items.length;
  }
  function openBase(item){editingBase=item;$('base-form').reset();$('base-title').textContent=item.title;$('base-amount').value=item.pricing_base_cents==null?'':(item.pricing_base_cents/100).toFixed(2);$('base-deposit').value=item.deposit_paid_cents==null?'':(item.deposit_paid_cents/100).toFixed(2);$('base-evidence').value=item.pricing_base_evidence||'';$('base-error').textContent='';$('base-dialog').showModal();}
  $('close-base').onclick=()=>$('base-dialog').close();
  $('base-form').addEventListener('submit',async event=>{event.preventDefault();try{if(demo)throw new Error('体验模式不会修改真实数据。');await api('/api/promotions/base',{method:'POST',body:JSON.stringify({item_id:editingBase.id,base_cents:cents($('base-amount').value),deposit_cents:cents($('base-deposit').value),evidence_note:$('base-evidence').value,confirmed:$('base-confirmed').checked})});$('base-dialog').close();await loadProject();}catch(e){$('base-error').textContent=e.message;}});
  function fillChoices(id,items,quantities=false){$(id).replaceChildren();for(const item of items){const row=checkbox(item.title,item.id);if(quantities){const q=el('input');q.type='number';q.min='1';q.max='999';q.value='1';q.className='qty';q.dataset.item=item.id;q.setAttribute('aria-label',item.title+'至少数量');row.append(q);}$(id).append(row);}}
  function refreshScopes(){const shop=$('offer-shop').value;const items=project.items.filter(x=>!shop||x.shop_id===shop);['scope-items','qualify-items','exclude-items'].forEach(id=>fillChoices(id,items));fillChoices('combo-items',items,true);}
  function refreshType(){const type=$('offer-type').value,shopLevel=['shop','item','pieces','bundle','tiered','repeat'].includes(type);
    $('amount-label').firstChild.textContent=['pieces','live'].includes(type)?'打几折（例如8.5）':type==='bundle'?'这组总价多少元':'减多少元';
    $('threshold-label').hidden=type==='bundle';$('tier-fields').hidden=type!=='tiered';$('amount-label').hidden=type==='tiered';$('offer-amount').required=type!=='tiered';
    $('offer-shop').required=shopLevel;$('offer-shop').querySelector('option[value=""]').disabled=shopLevel;if(shopLevel&&!$('offer-shop').value)$('offer-shop').selectedIndex=Math.min(1,$('offer-shop').options.length-1);
    $('phase').value=shopLevel?'20':({live:'30',category:'40',platform:'50',item_reduction:'10'})[type]||'60';$('min-quantity').value=type==='pieces'?'2':'1';$('stack').value='best';if(type==='bundle'){$('scope-mode').value='selected';$('scope-items').hidden=false;$('combo-enabled').checked=true;$('combo-items').hidden=false;}refreshScopes();}
  $('offer-type').onchange=refreshType;$('offer-shop').onchange=refreshScopes;
  $('scope-mode').onchange=()=>{$('scope-items').hidden=$('scope-mode').value!=='selected';};
  $('qualify-mode').onchange=()=>{$('qualify-items').hidden=$('qualify-mode').value!=='selected';};
  $('combo-enabled').onchange=()=>{$('combo-items').hidden=!$('combo-enabled').checked;};
  $('add-offer').onclick=()=>{if(demo)return;$('offer-form').reset();$('offer-error').textContent='';$('existing-resource').replaceChildren(new Option('新添加的券',''));for(const r of project.resources||[])$('existing-resource').append(new Option(r.name+'（剩余'+r.available+'）',r.id));$('offer-shop').replaceChildren(new Option('不限店铺（仅跨店券/红包）',''));const shops=new Map(project.items.filter(x=>x.shop_id).map(x=>[x.shop_id,x.shop_name||x.shop_id]));for(const [id,name] of shops)$('offer-shop').append(new Option(name,id));['scope-items','qualify-items','combo-items'].forEach(id=>$(id).hidden=true);refreshType();$('offer-dialog').showModal();};
  $('close-offer').onclick=()=>$('offer-dialog').close();
  $('offer-form').addEventListener('submit',async event=>{event.preventDefault();const save=$('offer-form').querySelector('button[type=submit]');save.disabled=true;try{
    if(demo)throw new Error('体验模式不会修改真实数据。');
    const type=$('offer-type').value,shop=$('offer-shop').value,shopLevel=['shop','item','pieces','bundle','tiered','repeat'].includes(type),kind=({pieces:'percent',live:'percent',bundle:'bundle_price',tiered:'tiered',repeat:'repeat_threshold'})[type]||'fixed';
    if(shopLevel&&!shop)throw new Error('请先确认商品属于哪个店铺，再配置店铺优惠。');
    const scope=$('scope-mode').value==='selected'?ids('scope-items'):[];if($('scope-mode').value==='selected'&&!scope.length)throw new Error('至少勾选一种适用商品。');
    const qualifying=$('qualify-mode').value==='selected'?ids('qualify-items'):[];if($('qualify-mode').value==='selected'&&!qualifying.length)throw new Error('至少勾选一种凑门槛商品。');
    const required={};if($('combo-enabled').checked){for(const check of $('combo-items').querySelectorAll('input[type=checkbox]:checked')){const v=check.closest('label').querySelector('.qty').value;if(!/^[1-9]\d{0,2}$/.test(v))throw new Error('搭配数量必须是1至999');required[check.value]=Number(v);}if(!Object.keys(required).length)throw new Error('请勾选必须一起买的商品。');}
    const fraction=Number($('offer-amount').value);if(['pieces','live'].includes(type)&&(!Number.isFinite(fraction)||fraction<=0||fraction>=10))throw new Error('折扣请填大于0、小于10的数字，例如8.5折。');
    const rule={id:'r-'+[...crypto.getRandomValues(new Uint8Array(10))].map(x=>x.toString(16).padStart(2,'0')).join(''),name:$('offer-name').value.trim(),kind,phase:Number($('phase').value),
      selection_mode:shopLevel&&$('stack').value==='best'?'best':type==='item_reduction'?'mandatory':'optional',exclusive_group:shopLevel&&$('stack').value==='best'?'shop:'+shop:({platform:'88vip-consumer-coupon',category:'category-coupon',live:'live-coupon'})[type]||'',exclusive_scope:shopLevel?'item':'payment',
      threshold_basis:$('basis').value,discount_basis:'remaining',threshold_cents:type==='bundle'?0:cents($('offer-threshold').value),amount_cents:['pieces','live','tiered'].includes(type)?0:cents($('offer-amount').value),
      discount_bps:['pieces','live'].includes(type)?Math.round(10000-fraction*1000):0,cap_cents:cents($('cap').value,true),min_quantity:int('min-quantity'),min_distinct_items:int('min-distinct'),
      item_ids:scope,qualifying_item_ids:qualifying,excluded_item_ids:ids('exclude-items'),required_item_quantities:required,shop_ids:shop?[shop]:[],
      resource_id:$('existing-resource').value||null,channels:$('channel').value?[$('channel').value]:[],confirmed:$('confirmed').checked,source_note:$('evidence').value.trim(),
      starts_at:$('offer-start').value?new Date($('offer-start').value).toISOString():null,ends_at:$('offer-end').value?new Date($('offer-end').value).toISOString():null,tiers:[]};
    if(type==='bundle' && (scope.length!==Object.keys(required).length || scope.some(id=>!required[id])))throw new Error('套餐一口价的适用商品和必买商品必须一致，并填写每种商品的精确数量。');
    if(type==='tiered'){rule.tiers=$('offer-tiers').value.trim().split('\n').filter(Boolean).map(line=>{const pair=line.split(/[,，]/);if(pair.length!==2)throw new Error('阶梯优惠请每行填写“门槛,优惠”，例如400,60');return {threshold_cents:cents(pair[0]),amount_cents:cents(pair[1]),min_quantity:1};});if(!rule.tiers.length)throw new Error('至少填写一档优惠');}
    await api('/api/promotions',{method:'POST',body:JSON.stringify({rule,is_coupon:!['pieces','bundle','tiered','repeat','item_reduction'].includes(type),available:int('coupon-count')})});
    $('offer-dialog').close();await loadProject();message('优惠已保存。修改优惠后，旧报价不会被自动当成新的实付价。',true);
  }catch(e){$('offer-error').textContent=e.message;}finally{save.disabled=false;}});
  function renderResult(data){
    const root=$('result');root.replaceChildren();root.append(el('span',data.demo?'虚构示例 · 不会扣款':'规则估算 · 付款前需核对','badge demo'));
    if(data.project_changed_during_search){root.append(el('h2','计算时清单发生变化，请重新计算'));return;}
    const strategies=data.comparison?.strategies||[],best=strategies.find(x=>x.key==='optimized')?.result;
    if(best?.cash_due_cents==null){root.append(el('h2','还缺信息，暂时不能估算'));for(const w of data.simulation?.warnings||[])root.append(el('p',w));return;}
    root.append(el('p','预计还需支付'),el('div',money(best.cash_due_cents),'total'));
    const metrics=el('div',null,'metrics');for(const s of strategies.filter(x=>x.key!=='optimized')){const c=el('div',null,'metric');c.append(el('small',s.name),el('b',money(s.result.cash_due_cents)));metrics.append(c);}root.append(metrics);
    const names=new Map(data.project.items.map(x=>[x.id,x.title]));const quotes=new Map(data.project.quotes.map(q=>[q.id,q]));
    best.steps.forEach((step,i)=>{const box=el('div',null,'step');box.append(el('h2',`第${i+1}笔 · ${money(step.cash_due_cents)}`),el('p',step.item_ids.map(id=>names.get(id)||id).join(' ＋ ')));
      const q=quotes.get(step.quote_id),breakdown=el('div',null,'breakdown');
      for(const d of q?.discount_breakdown||[]){const row=el('div',null,'discount-line');row.append(el('span',d.name),el('strong','−'+money(d.discount_cents)));breakdown.append(row);}
      if(!q?.discount_breakdown?.length)breakdown.append(el('p','这一笔没有采用已配置的优惠。'));
      box.append(breakdown);root.append(box);
    });
    if(data.explanation){const d=el('details'),s=el('summary','其他券为什么没用？');d.append(s);for(const r of data.explanation.rules.filter(x=>!x.applied))d.append(el('p',r.name+'：'+r.reason));root.append(d);}
    const d=el('details');d.append(el('summary','查看试算范围与提醒'));for(const w of [...(data.simulation?.warnings||[]),...(best.warnings||[])])d.append(el('p',w));root.append(d);
    root.append(el('p',data.notice||'不代表淘宝全部活动已经覆盖。','hint'));
  }
  $('estimate').onclick=()=>busy('正在比较店铺券和商品组合',async()=>{const result=demo?demoData:await api('/api/promotions/estimate',{method:'POST',body:'{}'});renderResult(result);tab('plan');});
  $('try-demo').onclick=()=>busy('正在准备购物车分组和组合优惠示例',async()=>{demoData=await api('/api/shopping/demo',{method:'POST',body:'{}'});demo=true;project=demoData.project;snapshot=demoData.cart;selection.clear();currentGroup='view:current';$('demo-note').hidden=false;renderCart();renderOffers();renderResult(demoData);tab('plan');});
  $('leave-demo').onclick=()=>busy('正在返回真实清单',async()=>{demo=false;demoData=null;snapshot=null;selection.clear();$('demo-note').hidden=true;await loadProject();renderCart();$('result').replaceChildren(el('h2','请重新计算真实清单'));tab('cart');});
  busy('正在读取本机清单',async()=>{await loadProject();renderCart();const requested=location.hash.slice(1);tab(['cart','offers','plan'].includes(requested)?requested:'cart');});
})();
