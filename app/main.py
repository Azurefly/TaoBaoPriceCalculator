from __future__ import annotations
import asyncio
import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field, ValidationError
from .models import Project, SolveOptions, StrictModel, Quote, PaymentRecord, utcnow
from .optimizer import optimize
from .pricing import simulate_project, generate_groups
from .store import Store, ConflictError, parse_coupon_text
from .workflow import diagnose, compare_strategies, record_payment
from .adapters.browser import BrowserAdapter, BrowserConfig

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('PROMOPATH_DATA_DIR', str(ROOT / '.data')))
TOKEN = secrets.token_urlsafe(32)
store = Store(DATA)
browser = BrowserAdapter(DATA)
JOBS: dict[str, dict] = {}
TASKS: dict[str, asyncio.Task] = {}

@asynccontextmanager
async def lifespan(app):
    yield
    for task in TASKS.values():
        task.cancel()
    await browser.close()

app = FastAPI(title='PromoPath · 通用优惠路径优化', version='0.2.0', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

@app.middleware('http')
async def local_security(request: Request, call_next):
    if request.url.path.startswith('/api/'):
        if not secrets.compare_digest(request.headers.get('x-local-token',''), TOKEN):
            return JSONResponse({'detail':'本地访问令牌缺失，请从本机应用首页重新打开'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
            return JSONResponse({'detail':'拒绝跨站请求'}, status_code=403)
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > 12_000_000):
            return JSONResponse({'detail':'请求过大'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    return response

@app.exception_handler(ConflictError)
async def conflict_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=409)


def revision_header(request: Request) -> str:
    revision = request.headers.get('x-project-revision')
    if not revision:
        raise HTTPException(428, '缺少项目版本号；请刷新页面后重试，避免覆盖其他操作。')
    return revision


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail':str(exc)}, status_code=400)

@app.get('/', response_class=HTMLResponse)
async def index():
    # 首页直接是新的一键界面：扫码后自动读购物车、出方案，无需手填应付金额。
    # 旧版“记商品→填应付金额”三步流程仍保留在 /advanced。
    html = (ROOT / 'app/static/shopping.html').read_text(encoding='utf-8')
    return html.replace('__LOCAL_TOKEN__', TOKEN)

app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')

@app.get('/api/meta')
async def meta():
    return {'version':'0.2.0', 'browser_permission_configured':browser.permitted(),
            'browser_opened':bool(browser.context), 'no_payment_capability':True,
            'permission_note':'许可引用仅为部署方自述，不是淘宝官方鉴权结果。',
            'browser_config':browser.config.model_dump()}

@app.get('/api/project')
async def get_project():
    p, rev = store.snapshot()
    return {'project':p.model_dump(mode='json'), 'fingerprint':p.fingerprint(), 'revision': rev}

@app.put('/api/project')
async def put_project(project: Project, request: Request):
    rev = store.save(project, revision_header(request))
    return {'saved':True, 'fingerprint':project.fingerprint(), 'revision': rev}

@app.get('/api/examples/{name}')
async def example(name: str):
    if name not in ('demo','historical','empty'):
        raise HTTPException(404, '示例不存在')
    if name == 'empty':
        return Project().model_dump(mode='json')
    p = Project.model_validate_json((ROOT / f'examples/{name}.json').read_text(encoding='utf-8'))
    return p.model_dump(mode='json')

class SimulateRequest(StrictModel):
    budget: int = Field(default=160, ge=1, le=1500)
    max_seconds: float = Field(default=8, ge=0.1, le=20)

@app.post('/api/simulate')
async def simulate(req: SimulateRequest):
    p, rev = store.snapshot()
    updated, info = await asyncio.to_thread(simulate_project, p, req.budget, None, req.max_seconds)
    with store.lock:
        store.save(updated, rev)
    return info

@app.post('/api/candidates')
async def candidates(req: SimulateRequest):
    p, rev = store.snapshot()
    groups, info = await asyncio.to_thread(generate_groups, p, req.budget)
    p.candidate_groups = groups
    store.save(p, rev)
    return {'groups':groups, 'coverage':info}

@app.post('/api/optimize')
async def solve(opts: SolveOptions):
    p, rev = store.snapshot()
    result = await asyncio.to_thread(optimize, p, opts)
    result['project_revision'] = rev
    result['project_changed_during_search'] = store.snapshot()[1] != rev
    if result['project_changed_during_search']:
        result['warnings'].insert(0, '计算期间项目被修改；结果不可用于付款，请重算。')
    return result

class QuoteInput(StrictModel):
    quote: Quote
    expected_fingerprint: str
    checkout_observed: bool = False

@app.post('/api/quotes')
async def add_quote(body: QuoteInput, request: Request):
    with store.lock:
        store.assert_revision(revision_header(request))
        p = store.load()
        if p.fingerprint() != body.expected_fingerprint:
            raise HTTPException(409, '页面上下文已过期，请刷新')
        q = body.quote
        old = next((q0 for q0 in p.quotes if q0.id == q.id), None)
        live_source = q.source in ('manual','checkout')
        if live_source and (old is None or old.source not in ('manual','checkout')
                            or old.context_fingerprint != p.fingerprint()
                            or old.captured_at != q.captured_at):
            if not body.checkout_observed or not q.evidence_note.strip():
                raise ValueError('新建/重新核价/将模拟报价转为真实报价，必须明确核对实际结算并填写证据备注')
        if q.context_fingerprint != p.fingerprint() and not body.checkout_observed:
            raise ValueError('上下文已变化；必须重新实际核价，不能只刷新报价指纹')
        if body.checkout_observed:
            q = q.model_copy(update={'context_fingerprint': p.fingerprint()})
        p.quotes = [x for x in p.quotes if x.id != q.id] + [q]
        store.save(p)
    return {'saved':True}

class ParseRequest(StrictModel):
    text: str = Field(max_length=100000)

@app.post('/api/parse-coupons')
async def parse_coupons(req: ParseRequest):
    return {'drafts':parse_coupon_text(req.text)}

@app.put('/api/browser/config')
async def configure_browser(cfg: BrowserConfig):
    browser.config = cfg
    return {'saved_in_memory':True, 'note':'重启后需重新校准；不自动信任旧页面选择器'}

@app.post('/api/browser/launch')
async def launch_browser():
    try:
        return await browser.launch()
    except ValueError:
        raise
    except Exception as e:
        raise HTTPException(503, f'浏览器启动失败：{type(e).__name__}。检查Playwright浏览器安装及桌面环境。')

@app.post('/api/browser/close')
async def close_browser():
    await browser.close()
    return {'closed':True}

@app.post('/api/browser/cart')
async def read_cart():
    return await browser.capture_cart()

class TrialRequest(StrictModel):
    item_ids: list[str]
    automatic: bool = False

@app.post('/api/browser/quote')
async def observe_quote(req: TrialRequest):
    p = store.load()
    if not req.item_ids or not set(req.item_ids) <= {x.id for x in p.items}:
        raise ValueError('报价组必须是项目中存在的商品')
    q = await (browser.trial(p, req.item_ids) if req.automatic else browser.observe(p, req.item_ids))
    with store.lock:
        current = store.load()
        if p.fingerprint() != current.fingerprint():
            raise HTTPException(409,'试算过程中项目已变化；报价已丢弃')
        current.quotes.append(q)
        store.save(current)
    return {'quote':q.model_dump(mode='json')}

class BatchRequest(StrictModel):
    groups: list[list[str]] = Field(min_length=1, max_length=12)

@app.post('/api/browser/batch')
async def batch(req: BatchRequest):
    browser.require_permission()
    if any(not t.done() for t in TASKS.values()):
        raise HTTPException(409,'已有试算任务，请先停止或完成')
    p = store.load()
    known = {x.id for x in p.items} - p.paid_item_ids()
    if any(not group or len(group) != len(set(group)) or not set(group) <= known for group in req.groups):
        raise ValueError('批量试算包含未知、重复或已付款商品')
    for old_id in list(JOBS):
        if len(JOBS) < 20:
            break
        if old_id not in TASKS or TASKS[old_id].done():
            JOBS.pop(old_id, None)
            TASKS.pop(old_id, None)
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {'status':'running', 'completed':0, 'total':len(req.groups), 'quotes':[], 'error':None}
    async def run():
        try:
            for group in req.groups:
                q = await browser.trial(p, group)
                with store.lock:
                    current = store.load()
                    if current.fingerprint() != p.fingerprint():
                        raise ValueError('项目已修改，停止旧状态试算')
                    current.quotes.append(q)
                    store.save(current)
                JOBS[jid]['quotes'].append(q.id)
                JOBS[jid]['completed'] += 1
            JOBS[jid]['status'] = 'completed'
        except asyncio.CancelledError:
            JOBS[jid]['status'] = 'stopped'
        except Exception as e:
            JOBS[jid]['status'] = 'paused'
            JOBS[jid]['error'] = str(e)
    TASKS[jid] = asyncio.create_task(run())
    return {'job_id':jid}

@app.get('/api/browser/jobs/{jid}')
async def get_job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404,'任务不存在')
    return JOBS[jid]

@app.post('/api/browser/jobs/{jid}/stop')
async def stop_job(jid: str):
    if jid not in TASKS:
        raise HTTPException(404,'任务不存在')
    TASKS[jid].cancel()
    return {'stop_requested':True}

from .refunds import RefundScenario, evaluate_refund

@app.post('/api/refund-scenario')
async def refund_scenario(req: RefundScenario):
    return evaluate_refund(req)


@app.get('/api/diagnostics')
async def diagnostics():
    return diagnose(store.load())


@app.post('/api/compare')
async def compare(opts: SolveOptions):
    p, rev = store.snapshot()
    result = await asyncio.to_thread(compare_strategies, p, opts)
    result['project_revision'] = rev
    result['project_changed_during_search'] = store.snapshot()[1] != rev
    return result


class PaymentInput(StrictModel):
    record: PaymentRecord
    actual_payment_confirmed: bool = False


@app.post('/api/payments/record')
async def payment_record(body: PaymentInput, request: Request):
    if not body.actual_payment_confirmed:
        raise ValueError('仅登记本人已经完成的真实付款；此接口不会下单或代付')
    with store.lock:
        p = store.load()
        existing = next((x for x in p.payments if x.id == body.record.id), None)
        if existing:
            if existing != body.record:
                raise ConflictError('同一付款记录ID内容冲突')
            return {'saved':True, 'idempotent':True, 'revision':store.revision(p)}
        rev = revision_header(request)
        store.assert_revision(rev)
        updated = record_payment(p, body.record)
        new_rev = store.save(updated, rev)
    return {'saved':True, 'revision':new_rev, 'paid_item_ids':sorted(updated.paid_item_ids()),
            'note':'实际付款已登记；权益余额已更新，旧报价失效。请核价后重新计算剩余商品。'}


@app.get('/api/backups')
async def backups():
    return {'backups':store.list_backups(), 'storage_hash':store.storage_hash()}


class RestoreInput(StrictModel):
    expected_storage_hash: str = Field(min_length=64, max_length=64)
    name: str
    acknowledged: bool = False


@app.post('/api/backups/restore')
async def restore(body: RestoreInput, request: Request):
    if not body.acknowledged:
        raise ValueError('恢复将替换当前项目，需要明确确认')
    rev = store.restore(body.name, expected_storage_hash=body.expected_storage_hash)
    return {'restored':True, 'revision':rev}

# Simple entry point and a non-destructive, one-click demo.
from .simple import create_router
app.include_router(create_router(store, ROOT))


# Optional shopping/cart tools extend the guided home without replacing it.
from .shopping import create_shopping_router
app.include_router(create_shopping_router(store, browser, ROOT))
