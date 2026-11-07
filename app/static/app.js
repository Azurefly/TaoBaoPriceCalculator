'use strict';
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const yen=n=>n==null?'待核对':'¥'+(n/100).toFixed(2);
const beijing=s=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）';
const token=$('meta[name="local-token"]').content;
let project=null, fingerprint='', revision='', editing=null, jobId=null, lastPlan=null, backupStorageHash='';
const uid=p=>p+'-'+Array.from(crypto.getRandomValues(new Uint8Array(6)),x=>x.toString(16).padStart(2,'0')).join('');
function msg(text,ok=false){const m=$('#message');m.textContent=text;m.className='message'+(ok?' ok':'');}
function cents(s,nullable=false){if(nullable&&!String(s).trim())return null;if(!/^\d+(?:\.\d{1,2})?$/.test(String(s).trim()))throw Error('金额须为非负数，最多两位小数');const [a,b='']=String(s).trim().split('.');return Number(a)*100+Number((b+'00').slice(0,2));}
async function api(path,method='GET',data){const r=await fetch('/api/'+path,{method,headers:{'Content-Type':'application/json','X-Local-Token':token,...(revision?{'X-Project-Revision':revision}:{})},body:data===undefined?undefined:JSON.stringify(data)});const out=await r.json();if(!r.ok)throw Error(typeof out.detail==='string'?out.detail:JSON.stringify(out.detail));return out;}
async function act(fn,button){if(button)button.disabled=true;try{return await fn()}catch(e){msg(e.message);throw e}finally{if(button)button.disabled=false}}
function bind(id,fn){$('#'+id).addEventListener('click',e=>act(fn,e.currentTarget).catch(()=>{}))}
async function refresh(){const d=await api('project');project=d.project;fingerprint=d.fingerprint;revision=d.revision;
 if(lastPlan && lastPlan.project_revision!==revision){lastPlan=null;$('#plan-result').textContent='数据或报价已变化，请重新计算；旧付款计划已作废。';$('#comparison-result').textContent='';}
 render();await refreshDiagnostics();}
function paidIds(){return new Set((project.payments||[]).flatMap(p=>p.item_ids));}
async function refreshDiagnostics(){const d=await api('diagnostics');$('#diagnostics').innerHTML=`<div class="section-title"><h2>数据核对清单</h2><span class="tag">未付必买 ${d.remaining_required} · 已付 ${d.paid_lines}</span></div>`+(d.issues.length?d.issues.map(x=>`<div class="check-row"><strong>${esc(x.title)}</strong><small>${esc(x.ids.slice(0,8).map(id=>title(id)).join('、'))}${x.ids.length>8?' 等 '+x.ids.length+' 项':''}</small><p>${esc(x.action)}</p></div>`).join(''):'<p>未发现基础缺项；仍需运行求解核对资源与时间约束。</p>');}

async function save(){await api('project','PUT',project);await refresh();}
function showTab(tab){$$('.tab').forEach(x=>x.classList.toggle('active',x.id===tab));$$('nav button').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));}
function title(id){return project.items.find(x=>x.id===id)?.title||id;}
function empty(cols,text){return `<tr><td colspan="${cols}" class="status">${esc(text)}</td></tr>`}
function render(){
 $('#project-title').textContent=project.name;
 const paid=paidIds();
 const sum=project.items.reduce((a,x)=>a+(x.display_price_cents||0),0), known=project.items.filter(x=>x.pricing_base_cents!==null&&x.deposit_confirmed).length;
 $('#stats').innerHTML=[['必买商品',project.items.filter(x=>x.required&&!paid.has(x.id)).length+' 件','可选凑单按保留计入成本'],['展示价算术和',yen(sum),'不是可支付总价或满减门槛'],['计价已确认',known+' / '+project.items.length,'未知基数禁止重复扣券'],['报价记录',project.quotes.length+' 组','真实结算与模型来源分开']].map(x=>`<div class="stat"><label>${esc(x[0])}</label><strong>${esc(x[1])}</strong><small>${esc(x[2])}</small></div>`).join('');
 $('#items').innerHTML=project.items.map(x=>`<tr><td>${esc(x.title)}<small>${esc(x.id)} · ${esc(x.shop_id)} · ${esc(x.sku)} × ${x.quantity}</small></td><td><span class="tag">${paid.has(x.id)?'已付，排除':x.required?'必买':'可选保留'}</span></td><td>${yen(x.display_price_cents)}<small>${esc(x.display_price_kind)}</small></td><td>${yen(x.pricing_base_cents)}<small>${esc(x.pricing_base_evidence)}</small></td><td>${yen(x.deposit_paid_cents)}<small>${x.deposit_confirmed?'口径已确认':'未确认'}</small></td><td><button data-edit="item" data-id="${esc(x.id)}">编辑</button></td></tr>`).join('')||empty(6,'还没有商品。可加载演示、历史清单，或添加自己的商品。');
 $('#resources-list').innerHTML=project.resources.map(x=>`<tr><td>${esc(x.name)}<small>${esc(x.id)}</small></td><td>${esc(x.kind)}</td><td>${x.available}</td><td>${yen(x.opportunity_cost_cents)}</td><td><button data-edit="resource" data-id="${esc(x.id)}">编辑</button></td></tr>`).join('')||empty(5,'请录入真实持有的券/红包实例；不要把汇总面额当作可用余额。');
 $('#rules-list').innerHTML=project.rules.map(x=>`<div class="rule-card"><div><strong>${esc(x.name)}</strong> <span class="tag ${x.confirmed?'':'warn'}">${x.confirmed?'已确认配置':'待确认'}</span><div class="status">阶段 ${x.phase} · 门槛 ${yen(x.threshold_cents)} · ${esc(x.threshold_basis)} · ${esc(x.kind)} · ${esc(x.exclusive_group||'无互斥组')}</div></div><button data-edit="rule" data-id="${esc(x.id)}">编辑规则</button></div>`).join('')||'<p>没有活动规则。真实结算报价模式不要求先重建所有规则。</p>';
 $('#quotes-list').innerHTML=project.quotes.slice(0,200).map(q=>`<tr><td>${esc(q.id)}<small>${esc(q.item_ids.map(title).join(' + '))}</small></td><td>${yen(q.cash_due_cents)}</td><td>${esc(Object.entries(q.consumed).map(([k,v])=>k+' × '+v).join('；')||'未标注/无')}</td><td><span class="tag">${esc(q.source)}</span><small>${q.ledger_complete?'账本已核对':'账本未核对'} / ${q.portable_state?'可跨状态复用':'限定观察状态'}${q.context_fingerprint!==fingerprint?' / 上下文失效':''}</small></td><td><button data-edit="quote" data-id="${esc(q.id)}">核对</button></td></tr>`).join('')||empty(5,'还没有报价。复制最终“应付金额”，并核对优惠消耗账本。');
 $('#payment-history').innerHTML=(project.payments||[]).map(p=>`<div class="check-row"><strong>${esc(p.id)} · ${yen(p.cash_paid_cents+p.extra_cost_cents)}</strong><small>${esc(p.item_ids.map(title).join(' + '))} · ${esc(beijing(p.paid_at))}</small></div>`).join('')||'<p>尚无已完成付款记录。这里仅记账，不会执行淘宝付款。</p>';
 $('#project-json').value=JSON.stringify(project,null,2);
}
function field(id,label,value='',type='text',hint=''){return `<div class="field"><label for="f-${id}">${esc(label)}</label><input id="f-${id}" type="${type}" value="${esc(value)}"><div class="hint">${esc(hint)}</div></div>`}
function selectField(id,label,value,options){return `<div class="field"><label for="f-${id}">${esc(label)}</label><select id="f-${id}">${options.map(([v,t])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(t)}</option>`).join('')}</select></div>`}
function check(id,label,val=false){return `<div class="field"><label><input type="checkbox" id="f-${id}" ${val?'checked':''}> ${esc(label)}</label></div>`}
function val(id){return document.getElementById('f-'+id).value.trim()}
function checked(id){return document.getElementById('f-'+id).checked}
function edit(kind,id){
 const seq={item:'items',resource:'resources',rule:'rules',quote:'quotes',payment:'quotes'}[kind];
 const obj=project[seq].find(x=>x.id===id);editing={kind,id,obj};$('#editor-error').textContent='';
 $('#editor-title').textContent={item:'商品与价格口径',resource:'优惠资源实例',rule:'活动规则（完整配置）',quote:'结算报价核对',payment:'登记已经完成的付款（仅记账）'}[kind];
 let html='';
 if(kind==='item'){
  const x=obj||{};
  html=field('id','稳定ID（购物车行/SKU，不是纯商品链接ID）',x.id||uid('item'))+field('title','商品名称',x.title||'')+`<div class="grid-two">${field('shop','店铺ID',x.shop_id||'')}${field('sku','SKU规格',x.sku||'')}</div><div class="grid-two">${field('qty','数量',x.quantity||1,'number')}${field('display','显示金额（元，当前行合计）',x.display_price_cents==null?'':(x.display_price_cents/100).toFixed(2))}</div>`+check('required','这是必买商品（取消则作为可选、实际保留的凑单商品）',x.required!==false)+`<div class="grid-two">${field('base','可独立核实的计价基数（元，未知留空）',x.pricing_base_cents==null?'':(x.pricing_base_cents/100).toFixed(2))}${field('deposit','已付定金（元，现货填0）',x.deposit_paid_cents==null?'':(x.deposit_paid_cents/100).toFixed(2))}</div>`+field('evidence','计价基数来源（无来源不允许模拟）',x.pricing_base_evidence||'')+check('depositConfirmed','已核实定金/尾款口径（现货确认定金为0）',!!x.deposit_confirmed);
 }else if(kind==='resource'){
  const x=obj||{};
  html=field('id','实例ID；每一张券保持唯一',x.id||uid('coupon'))+field('name','权益名称',x.name||'')+field('kind','类型：coupon / redpacket / coins / stored_value / quota / other',x.kind||'coupon')+field('available','剩余单位数（单张券=1；可拆余额可按分计）',x.available??1,'number')+field('cost','每单位机会成本（元；普通券0，购物金每分单位0.01）',((x.opportunity_cost_cents||0)/100).toFixed(2))+field('notes','备注',x.notes||'')+field('resource-start','生效时间（带时区ISO，未知留空）',x.starts_at||'')+field('resource-end','到期时间（带时区ISO，未知留空）',x.ends_at||'');
 }else if(kind==='rule'){
  const x=obj||{id:uid('rule'),name:'新活动满减',kind:'fixed',phase:10,threshold_basis:'remaining',discount_basis:'remaining',threshold_cents:100000,amount_cents:12500,discount_bps:0,cap_cents:null,min_quantity:1,item_ids:[],shop_ids:[],item_tags:[],channels:[],resource_id:null,resource_units:1,exclusive_group:'',starts_at:null,ends_at:null,confirmed:false,source_note:''};
  editing.defaults=x;
  html='<p>按具体活动配置；没有自动套用“双十一”的规则。金额以元输入。</p>'
   +field('id','规则ID',x.id)+field('name','规则名称',x.name)
   +selectField('rule-kind','优惠类型',x.kind,[['fixed','满额减固定金额'],['percent','折扣（支持封顶）'],['repeat_threshold','每满门槛减'],['quantity_fixed','件数门槛减']])
   +`<div class="grid-two">${field('threshold','金额门槛（元）',(x.threshold_cents/100).toFixed(2))}${field('amount','减免金额（元，折扣类型不用）',(x.amount_cents/100).toFixed(2))}${field('fold','折扣：9表示9折',10-x.discount_bps/1000)}${field('cap','折扣封顶（元，无封顶留空）',x.cap_cents==null?'':(x.cap_cents/100).toFixed(2))}${field('phase','计算阶段（小数字先计算）',x.phase,'number')}${field('minqty','最少件数',x.min_quantity,'number')}</div>`
   +selectField('threshold-basis','门槛按什么金额计算',x.threshold_basis,[['remaining','前序优惠后剩余金额'],['base','已核实的原始计价基数']])
   +selectField('discount-basis','折扣按什么金额计算',x.discount_basis,[['remaining','前序优惠后剩余金额'],['base','已核实的原始计价基数']])
   +selectField('selection','是否可跳过',x.selection_mode||'optional',[['optional','可选择使用'],['mandatory','符合条件必须自动生效']])
   +selectField('rule-resource','消耗哪张真实权益',x.resource_id||'',[['','不消耗单张券（仅已核实的自动活动）'],...project.resources.map(r=>[r.id,r.name])])
   +field('resource-units','本次消耗单位',x.resource_units,'number')+field('exclusive','单笔互斥组（如vip，一组最多用一项）',x.exclusive_group)
   +field('rule-items','适用商品ID（逗号分隔，空为不限）',(x.item_ids||[]).join(','))
   +field('rule-shops','适用店铺ID（逗号分隔，空为不限）',(x.shop_ids||[]).join(','))
   +field('rule-tags','匹配商品标签（逗号分隔）',(x.item_tags||[]).join(','))
   +field('channels','渠道（如web,app,live；空为不限定）',(x.channels||[]).join(','))
   +field('starts','生效时间（带时区ISO，未知留空）',x.starts_at||'')+field('ends','截止时间（带时区ISO，未知留空）',x.ends_at||'')
   +field('source-note','规则来源及适用范围核对备注',x.source_note||'')
   +check('rule-confirmed','已核对本条规则；未勾选不会参与模拟',!!x.confirmed);
 }else if(kind==='payment'){
  const q=obj||{};
  html='<p class="notice warning">这里只记录你在淘宝已经完成的付款，不会发起付款。请按实际订单修改金额和权益，不要直接抄估算。</p>'
   +field('id','记账ID（重复提交同ID不会再次扣券）',uid('paid'))
   +'<div class="field"><label>已付款商品</label>'+project.items.filter(x=>!paidIds().has(x.id)).map(x=>`<label><input type="checkbox" class="quote-item" value="${esc(x.id)}" ${(q.item_ids||[]).includes(x.id)?'checked':''}> ${esc(x.title)}</label>`).join('')+'</div>'
   +field('cash','本次实际新付现金（元，不含之前已付定金）',q.cash_due_cents==null?'':(q.cash_due_cents/100).toFixed(2))
   +field('extra','未包含在上项的额外现金费用（元）',((q.extra_cost_cents||0)/100).toFixed(2))
   +'<div class="grid-two">'+project.resources.map(r=>field('used-'+r.id,'实际消耗：'+r.name,q.consumed?.[r.id]||0,'number')+field('grant-'+r.id,'实际到账：'+r.name,0,'number')).join('')+'</div>'
   +field('paid-at','实际付款时间（带时区ISO）',new Date().toISOString())
   +field('note','核对备注（不要填账号、地址、订单号等隐私）','')
   +check('paid-confirmed','我已在淘宝完成付款，并核对上面全部实际数据',false);
 }else{
  const q=obj||{};
  html='<p>请录入最终仍需支付金额，不要再减定金。购物金等既有资产消耗也须登记。</p>'+field('id','报价ID',q.id||uid('quote'))+'<div class="field"><label>该笔包含的商品</label>'+project.items.map(x=>`<label><input type="checkbox" class="quote-item" value="${esc(x.id)}" ${(q.item_ids||[]).includes(x.id)?'checked':''}> ${esc(x.title)}</label>`).join('')+'</div>'+field('cash','仍需支付现金（元，包含最终运费/税费）',q.cash_due_cents==null?'':(q.cash_due_cents/100).toFixed(2))+'<div class="field"><label>实际消耗的权益（没有消耗填0）</label>'+project.resources.map(r=>`<label>${esc(r.name)} (${esc(r.id)}) <input class="consume" data-rid="${esc(r.id)}" type="number" min="0" value="${q.consumed?.[r.id]||0}"></label>`).join('')+'</div>'+check('ledger','我已核对本次完整优惠账本，未漏记红包/购物金等资源',!!q.ledger_complete)+check('portable','已验证：其他无关权益被使用后，这笔报价仍保持成立（否则限定当前余额状态）',!!q.portable_state)+field('note','证据备注：页面入口、时间、优惠明细',q.evidence_note||'')+field('extra','报价外额外现金费用（元，已含的不重复填）',((q.extra_cost_cents||0)/100).toFixed(2))+field('captured','实际观察时间（带时区ISO）',q.captured_at||new Date().toISOString())+field('valid-from','已知生效时间（可空）',q.valid_from||'')+field('valid-until','已知截止时间（可空；仍受缓存期限限制）',q.valid_until||'')+check('observed','我实际打开了结算页重新核价；不是仅编辑备注或将模拟金额冒充实价',false)+`<details><summary>保留并编辑前置条件与返券（JSON）</summary><textarea id="f-quote-advanced" rows="7">${esc(JSON.stringify({granted:q.granted||{},requires_done:q.requires_done||[],requires_zero:q.requires_zero||[]},null,2))}</textarea></details>`+'<p>勾选“可跨状态复用”需要证据，不是为了让算法返回结果。未勾选时，计算器会严格匹配观察时的权益余额。</p>';
 }
 $('#editor-fields').innerHTML=html;$('#editor').showModal();
}
$('#close-editor').onclick=()=>$('#editor').close();
$('#edit-form').onsubmit=async e=>{e.preventDefault();try{
 const {kind,id,obj}=editing;
 if(kind==='quote'){
  if(!checked('ledger'))throw Error('请先核实完整优惠账本；未核实的浏览器草稿不参与严格优化');
  const consumed=Object.create(null);$$('.consume').forEach(x=>{const n=Number(x.value);if(!Number.isInteger(n)||n<0)throw Error('消耗数量必须是非负整数');if(n)consumed[x.dataset.rid]=n});
  const observed=checked('observed');
  if((!obj||!['manual','checkout'].includes(obj.source))&&!observed)throw Error('新建或转换为真实报价，须实际核对结算页并勾选确认。模拟金额不能直接变成实价。');
  const advanced=JSON.parse($('#f-quote-advanced').value);
  if(Object.keys(advanced).some(k=>!['granted','requires_done','requires_zero'].includes(k)))throw Error('高级条件仅允许granted、requires_done、requires_zero');
  const q={...(obj||{}),...advanced,id:val('id'),item_ids:$$('.quote-item:checked').map(x=>x.value),cash_due_cents:cents(val('cash')),extra_cost_cents:cents(val('extra')),consumed,source:observed?'manual':obj.source,ledger_complete:true,portable_state:checked('portable'),observed_balances:observed?Object.fromEntries(project.resources.map(x=>[x.id,x.available])):obj.observed_balances,captured_at:val('captured'),valid_from:val('valid-from')||null,valid_until:val('valid-until')||null,context_fingerprint:observed?fingerprint:obj.context_fingerprint,evidence_note:val('note')};
  await api('quotes','POST',{quote:q,expected_fingerprint:fingerprint,checkout_observed:observed});
 }else if(kind==='payment'){
  if(!checked('paid-confirmed'))throw Error('只有本人已经完成付款才能登记。');
  const consumed=Object.create(null),granted=Object.create(null);
  for(const r of project.resources){for(const [prefix,map] of [['used-',consumed],['grant-',granted]]){const n=Number(val(prefix+r.id));if(!Number.isInteger(n)||n<0)throw Error('资源单位必须是非负整数');if(n)map[r.id]=n;}}
  await api('payments/record','POST',{actual_payment_confirmed:true,record:{id:val('id'),quote_id:obj?.id||null,item_ids:$$('.quote-item:checked').map(x=>x.value),cash_paid_cents:cents(val('cash')),extra_cost_cents:cents(val('extra')),consumed,granted,paid_at:val('paid-at'),evidence_note:val('note')}});
 }else{
  let x;
  if(kind==='item')x={...(obj||{}),id:val('id'),title:val('title'),shop_id:val('shop'),sku:val('sku'),quantity:Number(val('qty')),required:checked('required'),display_price_cents:cents(val('display'),true),display_price_kind:obj?.display_price_kind||'unknown',pricing_base_cents:cents(val('base'),true),pricing_base_evidence:val('evidence'),deposit_paid_cents:cents(val('deposit'),true),deposit_confirmed:checked('depositConfirmed')};
  if(kind==='resource')x={...(obj||{}),starts_at:val('resource-start')||null,ends_at:val('resource-end')||null,id:val('id'),name:val('name'),kind:val('kind'),available:Number(val('available')),opportunity_cost_cents:cents(val('cost')),notes:val('notes')};
  if(kind==='rule'){
   const fold=Number(val('fold'));if(!Number.isFinite(fold)||fold<0||fold>10)throw Error('折扣须为0到10之间的数字');
   const list=id=>val(id).split(/[,，]/).map(s=>s.trim()).filter(Boolean);
   x={...editing.defaults,id:val('id'),name:val('name'),kind:val('rule-kind'),phase:Number(val('phase')),selection_mode:val('selection'),threshold_basis:val('threshold-basis'),discount_basis:val('discount-basis'),threshold_cents:cents(val('threshold')),amount_cents:cents(val('amount')),discount_bps:Math.round((10-fold)*1000),cap_cents:cents(val('cap'),true),min_quantity:Number(val('minqty')),resource_id:val('rule-resource')||null,resource_units:Number(val('resource-units')),exclusive_group:val('exclusive'),item_ids:list('rule-items'),shop_ids:list('rule-shops'),item_tags:list('rule-tags'),channels:list('channels'),starts_at:val('starts')||null,ends_at:val('ends')||null,confirmed:checked('rule-confirmed'),source_note:val('source-note')};
  }
  const seq={item:'items',resource:'resources',rule:'rules'}[kind];const copy=structuredClone(project);copy[seq]=copy[seq].filter(y=>y.id!==id).concat(x);await api('project','PUT',copy);
 }
 $('#editor').close();await refresh();msg('已保存。修改价格/权益上下文后，旧报价需重新核对。',true);
 }catch(err){$('#editor-error').textContent=err.message;}
};
document.addEventListener('click',e=>{const b=e.target.closest('[data-edit]');if(b)edit(b.dataset.edit,b.dataset.id);});
$$('nav button').forEach(x=>x.onclick=()=>showTab(x.dataset.tab));
bind('new-item',()=>edit('item'));bind('new-resource',()=>edit('resource'));bind('new-rule',()=>edit('rule'));bind('new-quote',()=>edit('quote'));
async function loadExample(name){if(project.items.length&&!confirm('会替换当前项目。需要保留请先导出。继续？'))return;project=await api('examples/'+name);await save();showTab('overview');msg(name==='demo'?'已载入合成演示：到“结算报价”按规则模拟，然后切换规则模式优化。':'历史价格不是当前报价，缺少真实结算基数的商品不会被继续扣券。',true);}
bind('load-demo',()=>loadExample('demo'));bind('load-history',()=>loadExample('historical'));bind('reset',()=>loadExample('empty'));
bind('export',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(project,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='promopath-project.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
$('#import').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>12000000)throw Error('文件超过12MB');const p=JSON.parse(await file.text());await api('project','PUT',p);await refresh();msg('项目已导入',true)}catch(err){msg(err.message)}finally{e.target.value=''}};
bind('refresh-json',refresh);bind('save-json',async()=>{const p=JSON.parse($('#project-json').value);await api('project','PUT',p);await refresh();msg('结构校验通过，已保存。',true)});
bind('parse-coupons',async()=>{$('#coupon-result').textContent=JSON.stringify(await api('parse-coupons','POST',{text:$('#coupon-text').value}),null,2)});
bind('candidates',async()=>{const r=await api('candidates','POST',{budget:Number($('#budget').value)});await refresh();$('#simulation-result').textContent=JSON.stringify(r,null,2);msg('已生成候选；未采价组合不构成真实报价。',true)});
bind('simulate',async()=>{const r=await api('simulate','POST',{budget:Number($('#budget').value)});await refresh();$('#simulation-result').textContent=JSON.stringify(r,null,2);msg('规则模拟完成；可到优化路径切换“规则演示模式”。',true)});
bind('optimize',async()=>{
 const r=await api('optimize','POST',{mode:$('#solve-mode').value,step_seconds:Number($('#step-seconds').value)});
 lastPlan=r;
 const label={candidate_optimal:'已输入有效报价集合内最优',best_found:'搜索预算内找到的最低价',no_feasible_plan:'当前证据不足，无法形成完整付款计划',search_incomplete:'搜索预算耗尽，尚未找到完整方案'}[r.status];
 if(r.project_changed_during_search)throw Error('计算期间项目已变化，请刷新重算；本结果不可用于付款。');
 $('#plan-result').className='';
 $('#plan-result').innerHTML=`<div class="panel"><div class="section-title"><div><h2>${esc(label)}</h2><p>${esc(r.scope)} · ${r.accepted_quotes} 条有效报价 · 搜索 ${r.searched_nodes} 个状态</p></div><span class="tag">${esc(r.mode)}</span></div><div class="stats"><div class="stat"><label>未来现金支出</label><strong>${yen(r.cash_due_cents)}</strong></div><div class="stat"><label>含购物金等机会成本</label><strong>${yen(r.economic_cost_cents)}</strong></div><div class="stat"><label>付款批次</label><strong>${r.order_count??'—'}</strong></div><div class="stat"><label>候选集合内证明差距</label><strong>${yen(r.gap_cents)}</strong></div></div>${r.steps.map((s,i)=>`<div class="step"><h3>第 ${i+1} 笔 · ${yen(s.cash_due_cents)}</h3>${r.mode==='strict'?`<button data-edit="payment" data-id="${esc(s.quote_id)}">已在淘宝付完？登记实际结果</button>`:''}<div>${esc(s.item_ids.map(title).join(' + '))}</div><div class="status">消耗：${esc(JSON.stringify(s.consumed))} · 来源 ${esc(s.source)}<br>最早操作 ${esc(beijing(s.earliest_start))} · 付款前重新核价</div></div>`).join('')}${r.warnings.map(w=>`<p class="footnote">${esc(w)}</p>`).join('')}${r.missing_item_ids.length?`<p class="bad">缺少可用报价：${esc(r.missing_item_ids.map(title).join('、'))}</p>`:''}<details><summary>查看排除原因与搜索证据</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details></div>`;
});
async function refreshMeta(){const d=await api('meta');$('#browser-config').value=JSON.stringify(d.browser_config,null,2);$('#browser-status').textContent=(d.browser_permission_configured?'已配置许可引用（未经程序官方验证）':'未配置平台书面许可，自动化入口关闭')+'；浏览器'+(d.browser_opened?'已打开':'未打开');}
bind('launch-browser',async()=>{const r=await api('browser/launch','POST',{});await refreshMeta();msg(r.note||'浏览器已打开',true)});bind('close-browser',async()=>{await api('browser/close','POST',{});await refreshMeta()});
bind('save-browser-config',async()=>{await api('browser/config','PUT',JSON.parse($('#browser-config').value));msg('本次配置已保存；先用受控页面核对，不要猜测选择器。',true)});
bind('capture-cart',async()=>{const r=await api('browser/cart','POST',{});$('#browser-result').textContent=JSON.stringify(r,null,2);if(confirm('将当前可见商品草稿合并到项目？现有同ID商品不会覆盖。')){const existing=new Set(project.items.map(x=>x.id));project.items.push(...r.items.filter(x=>!existing.has(x.id)));await save();msg(r.warning)}});
async function browserQuote(automatic){const ids=$('#browser-items').value.split(',').map(x=>x.trim()).filter(Boolean);const r=await api('browser/quote','POST',{item_ids:ids,automatic});$('#browser-result').textContent=JSON.stringify(r,null,2);await refresh();msg('报价已作为草稿采集。请到“结算报价”核对实际权益消耗。',true);}
bind('observe-quote',()=>browserQuote(false));bind('trial-quote',()=>browserQuote(true));
bind('batch-trial',async()=>{const groups=project.candidate_groups.slice(0,3);if(!groups.length)throw Error('先生成候选分组');const d=await api('browser/batch','POST',{groups});jobId=d.job_id;async function poll(){try{const r=await api('browser/jobs/'+jobId);$('#job-status').textContent=JSON.stringify(r,null,2);if(r.status==='running')setTimeout(poll,1500);else await refresh()}catch(e){msg(e.message)}}await poll()});
bind('stop-trial',async()=>{if(jobId)await api('browser/jobs/'+jobId+'/stop','POST',{})});
refresh().then(refreshMeta).catch(e=>msg(e.message));

bind('refund-scenario',async()=>{const confirmed=$('#rf-confirmed').checked;const r=await api('refund-scenario','POST',{cash_paid_total_cents:cents($('#rf-paid').value),cash_refund_cents:cents($('#rf-refund').value),stored_principal_spent_cents:cents($('#rf-principal').value),stored_principal_returned_cents:cents($('#rf-principal-back').value),return_shipping_cents:cents($('#rf-shipping').value),baseline_keep_economic_cost_cents:cents($('#rf-baseline').value,true),additional_clawback_cents:cents($('#rf-clawback').value),refund_amount_confirmed:confirmed,all_adjustments_confirmed:confirmed});$('#refund-result').textContent='保留商品的现金净成本：'+yen(r.net_cash_cost_cents)+'\n含购物金本金的净成本：'+yen(r.net_economic_cost_cents)+'\n'+(r.status==='scenario_only'?'情景估算，尚非确定收益':'已确认金额核算')+'\n'+(r.saving_vs_baseline_cents==null?'未录入不凑单对照':('相对不凑单'+(r.saving_vs_baseline_cents>=0?'节省：':'多花：')+yen(Math.abs(r.saving_vs_baseline_cents))))+'\n'+r.note+'\n'+r.warning});


bind('record-payment',()=>edit('payment'));
bind('compare',async()=>{
 const r=await api('compare','POST',{mode:$('#solve-mode').value,step_seconds:Number($('#step-seconds').value)});
 if(r.project_changed_during_search)throw Error('比较过程中数据改变，请刷新后重新比较。');
 $('#comparison-result').innerHTML='<div class="panel"><h2>同一账本下的方案比较</h2><div class="comparison-grid">'+r.strategies.map(row=>`<div class="stat ${row.key===r.recommended_key?'recommended':''}"><label>${esc(row.name)}</label><strong>${yen(row.result.economic_cost_cents)}</strong><small>现金 ${yen(row.result.cash_due_cents)} · ${row.result.order_count??'—'} 笔</small><p>${esc(row.result.status==='candidate_optimal'?'当前候选内已求得最优':row.result.status==='best_found'?'预算内找到的可行方案':'尚无完整可行方案')}</p></div>`).join('')+'</div><p class="footnote">'+esc(r.note)+'</p></div>';
});
bind('export-plan',()=>{
 if(!lastPlan||lastPlan.project_revision!==revision)throw Error('先计算当前数据的付款路径。');
 const lines=['# PromoPath 付款计划（付款前需重新核价）','',lastPlan.scope,'模式：'+lastPlan.mode,'未来现金：'+yen(lastPlan.cash_due_cents),'经济成本：'+yen(lastPlan.economic_cost_cents),''];
 for(const [i,s] of lastPlan.steps.entries())lines.push(`## 第${i+1}笔：${yen(s.cash_due_cents)}`,s.item_ids.map(title).join(' + '),'消耗权益：'+JSON.stringify(s.consumed),'最早开始：'+beijing(s.earliest_start),'最迟有效：'+(s.effective_deadline?beijing(s.effective_deadline):'未单独指定，仍须复核'),'');
 lines.push(...lastPlan.warnings);
 const url=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='promopath-payment-plan.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
bind('refresh-backups',async()=>{const d=await api('backups');backupStorageHash=d.storage_hash;$('#backup-list').innerHTML=d.backups.map(x=>`<option value="${esc(x.name)}">${esc(x.name)} · ${x.size} bytes</option>`).join('')||'<option value="">暂无备份</option>';msg('已读取本地备份列表；仅含项目，不含登录态。',true)});
bind('restore-backup',async()=>{const name=$('#backup-list').value;if(!name)throw Error('先选择一个备份');if(!confirm('恢复会替换当前项目（包括付款记账状态），确定继续？'))return;await api('backups/restore','POST',{name,acknowledged:true,expected_storage_hash:backupStorageHash});await refresh();msg('已恢复备份；请核对实际付款状态和权益余额。',true)});
