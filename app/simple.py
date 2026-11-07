"""Guided shopping UI. Demo calculations never load or write the user's Store."""
from __future__ import annotations
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from .models import Project, SolveOptions, utcnow
from .pricing import simulate_project
from .workflow import compare_strategies


def calculate_demo(root: Path) -> dict:
    sample = Project.model_validate_json((root / 'examples/demo.json').read_text('utf-8'))
    now = utcnow()
    sample, _ = simulate_project(sample, 100, now, 5)
    comparison = compare_strategies(sample, SolveOptions(mode='model', starts_at=now, step_seconds=60, max_seconds=3))
    return {'demo': True, 'project': sample.model_dump(mode='json'), 'comparison': comparison,
            'notice': '示例商品和优惠都是虚构的。不会读取、替换或修改你的真实购物清单。'}


def create_router(store, root: Path) -> APIRouter:
    router = APIRouter()
    calculation_lock = asyncio.Lock()

    @router.get('/advanced', response_class=HTMLResponse)
    async def advanced():
        from .main import TOKEN
        html = (root / 'app/static/advanced.html').read_text('utf-8')
        html = html.replace('<script defer src="/static/app.js"></script>',
            '<script defer src="/static/advanced-entry.js"></script><script defer src="/static/app.js"></script>')
        html = html.replace('<button id="load-demo">运行演示</button>',
            '<a href="/" class="button">返回简单模式</a><button id="load-demo">一键体验（不改清单）</button>')
        return html.replace('__LOCAL_TOKEN__', TOKEN)

    @router.post('/api/simple/demo')
    async def demo():
        if calculation_lock.locked():
            raise HTTPException(409, '正在计算，请稍后重试。')
        async with calculation_lock:
            return await asyncio.to_thread(calculate_demo, root)

    @router.post('/api/simple/plan')
    async def plan():
        if calculation_lock.locked():
            raise HTTPException(409, '正在计算，请稍后重试。')
        async with calculation_lock:
            project, revision = store.snapshot()
            comparison = await asyncio.to_thread(compare_strategies, project, SolveOptions(mode='strict', max_seconds=4))
            changed = store.snapshot()[1] != revision
            return {'demo': False, 'project': project.model_dump(mode='json'), 'comparison': comparison,
                    'project_revision': revision, 'fingerprint': project.fingerprint(), 'project_changed_during_search': changed}

    return router
