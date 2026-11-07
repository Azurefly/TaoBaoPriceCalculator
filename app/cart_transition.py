"""Do not label stale rows with a newly selected cart tab's name."""
from __future__ import annotations


def row_signature(snapshot: dict) -> tuple:
    # Do not include derived group IDs: the tab alone changes those even while
    # the page is still displaying the previous group's rows.
    return tuple(sorted((str(x.get('line_id', '')), str(x.get('sku', '')),
                         str(x.get('quantity', ''))) for x in snapshot.get('items', [])))


async def wait_for_group(read_snapshot, pause, expected_id: str, previous: dict,
                         attempts: int = 30, stable_samples: int = 3) -> dict:
    old = row_signature(previous)
    last = None
    stable = 0
    for _ in range(attempts):
        current = await read_snapshot()
        signature = row_signature(current)
        ready = (current.get('active_group_id') == expected_id
                 and current.get('profile') and not current.get('loading')
                 and not current.get('errors') and signature != old)
        if ready:
            stable = stable + 1 if signature == last else 1
            last = signature
            if stable >= stable_samples:
                return current
        else:
            stable = 0
            last = None
        await pause(350)
    raise ValueError('分组标签已切换，但商品列表尚未确认更新。请在淘宝手动切换并确认后，再点“读取商品”；未使用旧列表替代。')
