"""一键导入 → 出方案 → 删除重算：不访问淘宝，使用真实结构快照。"""
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from app.cart import normalized_capture
from app.models import Project, SolveOptions, utcnow
from app.pricing import simulate_project
from app.workflow import compare_strategies
from app.store import Store
from app.shopping import create_shopping_router
from app.adapters.browser import BrowserAdapter


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path)
    app = FastAPI()
    app.include_router(create_shopping_router(store, BrowserAdapter(tmp_path), Path(__file__).resolve().parents[1]))
    @app.exception_handler(ValueError)
    async def error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)
    return TestClient(app), store

def _project(snapshot, selected):
    items = []
    for row in snapshot['items']:
        if not row['can_import']:
            continue
        state = ('presale_pending' if row.get('price_label') == '定金'
                 else 'selected' if row['line_id'] in selected else 'unselected')
        items.append(dict(id=row['id'], title=row['title'], source_line_id=row['line_id'],
                          shop_id=row['shop_id'], quantity=row['quantity'], required=state == 'selected',
                          calculation_state=state, display_price_cents=row['display_price_cents'],
                          pricing_base_cents=row['display_price_cents'] if state != 'presale_pending' else None,
                          pricing_base_evidence='购物车行实付', deposit_paid_cents=0 if state != 'presale_pending' else None,
                          deposit_confirmed=state != 'presale_pending'))
    return Project.model_validate({'items': items})


def _raw():
    return {
        'profile': 'div.trade-cart-item-info', 'groups': [], 'summary': {},
        'items': [
            {'line_id': 'sku:1:1', 'title': 'A', 'shop_id': 's1', 'quantity': 1,
             'display_price_text': '39.81', 'price_scope': 'unit', 'selected': True},
            {'line_id': 'sku:2:2', 'title': 'B', 'shop_id': 's2', 'quantity': 2,
             'display_price_text': '334.82', 'price_scope': 'unit', 'selected': True},
            {'line_id': 'sku:3:3', 'title': 'C', 'shop_id': 's3', 'quantity': 1,
             'display_price_text': '245.41', 'price_scope': 'unit', 'selected': True},
            {'line_id': 'sku:4:4', 'title': '预售', 'shop_id': 's4', 'quantity': 3,
             'display_price_text': '30', 'price_scope': 'unit', 'selected': True,
             'price_label': '定金', 'reference_price_text': '163.00'},
        ]}


def test_selected_plan_matches_cart_and_deletion_recomputes():
    snapshot = normalized_capture(_raw(), 'conn')
    selected = {'sku:1:1', 'sku:2:2', 'sku:3:3'}
    project = _project(snapshot, selected)
    assert sum(x.pricing_base_cents for x in project.items if x.required) == 95486
    assert next(x for x in project.items if x.title == '预售').calculation_state == 'presale_pending'
    assert next(x for x in project.items if x.title == '预售').display_price_cents is None

    simulated, _ = simulate_project(project, 50)
    result = compare_strategies(simulated, SolveOptions(mode='model', starts_at=utcnow(), step_seconds=0))
    assert result['strategies'][0]['result']['cash_due_cents'] == 95486

    project.items = [x for x in project.items if x.title != 'B']
    simulated, _ = simulate_project(project, 50)
    result = compare_strategies(simulated, SolveOptions(mode='model', starts_at=utcnow(), step_seconds=0))
    assert result['strategies'][0]['result']['cash_due_cents'] == 28522


def test_delete_endpoint_removes_item_and_dependent_rules(client):
    c, store = client
    project = Project.model_validate({
        'items': [{'id': 'a', 'title': 'A', 'required': True},
                  {'id': 'b', 'title': 'B', 'required': True}],
        'rules': [{'id': 'r', 'name': 'A 的券', 'item_ids': ['a'], 'confirmed': True, 'source_note': 'test'}]})
    store.save(project)
    _, revision = store.snapshot()
    response = c.delete('/api/items/a', headers={'x-project-revision': revision})
    assert response.status_code == 200
    saved = store.load()
    assert [x.id for x in saved.items] == ['b']
    assert saved.rules == []
