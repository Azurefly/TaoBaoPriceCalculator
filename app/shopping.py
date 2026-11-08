"""Simple promotion editing and non-destructive estimates; guarded cart bridge."""
from __future__ import annotations
import asyncio
from pathlib import Path
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import Field
from .models import Project, Rule, Resource, Item, SolveOptions, StrictModel, Money, utcnow
from .pricing import simulate_project
from .promotion_engine import explain_group
from .workflow import compare_strategies
from .cart import create_cart_router, normalized_capture


class PromotionInput(StrictModel):
    rule: Rule
    is_coupon: bool = True
    available: int = Field(default=1, ge=1, le=20)


class BaseInput(StrictModel):
    item_id: str
    base_cents: Money
    deposit_cents: Money = 0
    evidence_note: str = Field(min_length=1, max_length=1000)
    confirmed: bool = False


class GroupInput(StrictModel):
    item_ids: list[str] = Field(min_length=1,max_length=28)


async def calculate(project, seconds=4):
    now=utcnow()
    simulated, info=await asyncio.to_thread(simulate_project, project, 300, now, seconds)
    result=await asyncio.to_thread(compare_strategies, simulated,
        SolveOptions(mode='model',starts_at=now,max_seconds=seconds,step_seconds=60))
    return {'project':simulated.model_dump(mode='json'),'comparison':result,'simulation':info,
            'model_only':True,'notice':'仅按已确认规则估算；最终金额和是否能叠加，以淘宝结算页为准。'}


def demo_project():
    p=Project(name='示例：店铺券、搭配券和跨店券',items=[
        Item(id='wash',title='示例洁面',shop_id='s1',shop_name='示例护肤店',pricing_base_cents=18000,pricing_base_evidence='虚构示例',deposit_paid_cents=0,deposit_confirmed=True),
        Item(id='cream',title='示例面霜',shop_id='s1',shop_name='示例护肤店',pricing_base_cents=24000,pricing_base_evidence='虚构示例',deposit_paid_cents=0,deposit_confirmed=True),
        Item(id='serum',title='示例精华',shop_id='s2',shop_name='示例品牌店',pricing_base_cents=30000,pricing_base_evidence='虚构示例',deposit_paid_cents=0,deposit_confirmed=True),
        Item(id='paper',title='示例纸巾',shop_id='s3',shop_name='示例日用品店',pricing_base_cents=4000,pricing_base_evidence='虚构示例',deposit_paid_cents=0,deposit_confirmed=True)],
        resources=[Resource(id='shop',name='护肤店满400减60'),Resource(id='bundle',name='洁面搭面霜减70'),
                   Resource(id='serum-c',name='精华专享50'),Resource(id='platform',name='跨店满600减80')])
    common=dict(phase=20,shop_ids=['s1'],selection_mode='best',exclusive_group='shop-s1',exclusive_scope='item',confirmed=True,source_note='虚构示例规则，非淘宝活动')
    p.rules=[Rule(id='shop-c',name='护肤店满400减60',threshold_cents=40000,amount_cents=6000,resource_id='shop',**common),
        Rule(id='pieces',name='护肤店满2件85折',kind='percent',min_quantity=2,discount_bps=1500,**common),
        Rule(id='bundle-r',name='洁面与面霜一起买，面霜减70',item_ids=['cream'],qualifying_item_ids=['wash','cream'],
             required_item_quantities={'wash':1,'cream':1},amount_cents=7000,resource_id='bundle',**common),
        Rule(id='serum-r',name='精华专享减50',phase=20,item_ids=['serum'],shop_ids=['s2'],amount_cents=5000,resource_id='serum-c',confirmed=True,source_note='虚构示例'),
        Rule(id='platform-r',name='跨店满600减80',phase=40,threshold_cents=60000,amount_cents=8000,resource_id='platform',confirmed=True,source_note='虚构示例，店铺优惠后算门槛')]
    return Project.model_validate(p.model_dump())


def create_shopping_router(store,browser,root:Path):
    router=APIRouter();router.include_router(create_cart_router(store,browser,root));lock=asyncio.Lock()

    @router.get('/shopping-tools',response_class=HTMLResponse)
    async def page():
        from .main import TOKEN
        return (root/'app/static/shopping.html').read_text('utf-8').replace('__LOCAL_TOKEN__',TOKEN)

    def revision(request):
        rev=request.headers.get('x-project-revision')
        if not rev: raise HTTPException(428,'页面已过期，请刷新后操作')
        return rev

    @router.post('/api/promotions')
    async def save_promotion(body:PromotionInput,request:Request):
        with store.lock:
            rev=revision(request);store.assert_revision(rev);p=store.load();r=body.rule
            if r.confirmed and not r.source_note.strip(): raise ValueError('请填写核对依据，不把推测设为已确认')
            old=next((x for x in p.rules if x.id==r.id),None)
            rid=old.resource_id if old else body.rule.resource_id
            if rid and rid not in {x.id for x in p.resources}: raise ValueError("所选优惠券不存在，请刷新后选择")
            if body.is_coupon:
                if not rid:
                    rid='coupon-'+uuid.uuid4().hex[:16]
                    p.resources.append(Resource(id=rid,name=r.name,available=body.available,starts_at=r.starts_at,ends_at=r.ends_at))
                r=r.model_copy(update={'resource_id':rid})
            else:
                r=r.model_copy(update={'resource_id':None})
            p.rules=[x for x in p.rules if x.id!=r.id]+[r]
            p=Project.model_validate(p.model_dump());new=store.save(p,rev)
        return {'saved':True,'revision':new,'rule':r.model_dump(mode='json')}

    @router.delete('/api/promotions/{rule_id}')
    async def delete_promotion(rule_id:str,request:Request):
        with store.lock:
            rev=revision(request);store.assert_revision(rev);p=store.load()
            if rule_id not in {r.id for r in p.rules}: raise HTTPException(404,'该优惠已不存在')
            p.rules=[r for r in p.rules if r.id!=rule_id];store.save(p,rev)
        return {'deleted':True}

    @router.post('/api/promotions/base')
    async def save_base(body:BaseInput,request:Request):
        if not body.confirmed or not body.evidence_note.strip(): raise ValueError('必须确认这是优惠前货款，不是购物车券后展示价')
        if body.deposit_cents>body.base_cents: raise ValueError('已付定金不能大于优惠前货款')
        with store.lock:
            rev=revision(request);store.assert_revision(rev);p=store.load()
            item=next((x for x in p.items if x.id==body.item_id),None)
            if item is None: raise ValueError('商品不存在')
            if item.id in p.paid_item_ids(): raise ValueError('不能修改已付款商品')
            item.pricing_base_cents=body.base_cents;item.deposit_paid_cents=body.deposit_cents
            item.deposit_confirmed=True;item.pricing_base_evidence=body.evidence_note
            store.save(Project.model_validate(p.model_dump()),rev)
        return {'saved':True}

    @router.delete('/api/items/{item_id}')
    async def delete_item(item_id:str,request:Request):
        with store.lock:
            rev=revision(request);store.assert_revision(rev);p=store.load()
            if item_id not in {x.id for x in p.items}: raise HTTPException(404,'该商品已不存在')
            if item_id in p.paid_item_ids(): raise ValueError('不能删除已付款商品')
            p.items=[x for x in p.items if x.id!=item_id]
            keep=[]
            for r in p.rules:
                touched=set(r.item_ids+r.qualifying_item_ids+r.excluded_item_ids)|set(r.required_item_quantities)
                if item_id in touched: continue
                keep.append(r)
            p.rules=keep
            p.quotes=[q for q in p.quotes if item_id not in q.item_ids]
            new=store.save(Project.model_validate(p.model_dump()),rev)
        return {'deleted':True,'revision':new,'items':len(p.items)}

    @router.post('/api/promotions/explain')
    async def explain(body:GroupInput):
        p=store.load()
        if len(set(body.item_ids))!=len(body.item_ids) or not set(body.item_ids)<={x.id for x in p.items}:
            raise ValueError('商品选择包含重复或未知商品')
        return explain_group(p,body.item_ids)

    @router.post('/api/promotions/estimate')
    async def estimate():
        if lock.locked(): raise HTTPException(409,'已经在计算，请勿重复点击')
        async with lock:
            p,rev=store.snapshot();result=await calculate(p)
            result.update(project_revision=rev,project_changed_during_search=store.snapshot()[1]!=rev)
            # Explain the actual selected batch, not a misleading all-items bundle.
            quotes={q['id']:q for q in result['project']['quotes']}
            best=next((s['result'] for s in result['comparison']['strategies'] if s['key']=='optimized'),{})
            result['explanations']=[]
            for step in best.get('steps',[]):
                q=quotes.get(step['quote_id'],{})
                result['explanations'].append({'item_ids':step['item_ids'],'applied_rules':q.get('applied_rules',[]),
                    'discount_breakdown':q.get('discount_breakdown',[]),'notes':q.get('notes',[])})
            return result

    @router.post('/api/shopping/demo')
    async def demo():
        if lock.locked(): raise HTTPException(409,'已经在计算，请稍后重试')
        async with lock:
            p=demo_project();result=await calculate(p)
            result['demo']=True
            rows=[{'line_id':x.id,'title':x.title,'product_id':'example-'+x.id,'sku':'示例规格','quantity':1,
                   'shop_id':x.shop_id,'shop_name':x.shop_name,'display_price_text':str(x.pricing_base_cents/100),
                   'price_scope':'line','group_ids':['skin' if x.id!='paper' else 'daily'],
                   'group_names':['示例护肤组' if x.id!='paper' else '示例日用品组'],'observed_promotions':[]} for x in p.items]
            raw={'items':rows,'groups':[{'id':'all','name':'示例全部','active':True},
                  {'id':'skin','name':'示例护肤组','active':False},{'id':'daily','name':'示例日用品组','active':False}],
                 'active_group_id':'all','profile':'synthetic-demo','warnings':['虚构购物车，不连接淘宝，不修改真实项目。']}
            result['cart']=normalized_capture(raw,'synthetic-demo')
            result['explanation']=explain_group(p,[x.id for x in p.items])
            return result
    return router
