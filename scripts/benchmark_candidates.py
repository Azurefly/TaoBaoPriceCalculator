import sys,json,time
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,sys.argv[1] if len(sys.argv)>1 else str(Path(__file__).resolve().parents[1]))
from app.models import Project,Item,Rule,SolveOptions
from app.pricing import simulate_project
from app.optimizer import optimize
now=datetime.now(timezone.utc)
p=Project(items=[Item(id=f'i{i:02d}',title=f'合成商品{i}',pricing_base_cents=10000,
    pricing_base_evidence='合成基数，无运费，非平台报价',deposit_paid_cents=0,deposit_confirmed=True) for i in range(28)],
    rules=[Rule(id='r',name='测试每单满500减100（可多单使用）',threshold_cents=50000,amount_cents=10000,confirmed=True)])
t=time.perf_counter();p,info=simulate_project(p,160,now);modeltime=time.perf_counter()-t
r=optimize(p,SolveOptions(mode='model',starts_at=now,step_seconds=0,max_seconds=5))
print(json.dumps({'case':'28件合成商品，每件100元，每单满500减100，不含运费税费；非淘宝规则',
'candidate_groups':len(p.candidate_groups),'quotes':len(p.quotes),'simulation_ms':round(modeltime*1000),
'cash_due_cents':r['cash_due_cents'],'status':r['status'],'orders':r['order_count'],'nodes':r['searched_nodes'],
'search_ms':r['runtime_ms'],'steps':[{k:s[k] for k in ('item_ids','cash_due_cents')} for s in r['steps']]},ensure_ascii=False,indent=2))
